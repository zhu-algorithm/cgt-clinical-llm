"""ZhaoYou-Med — LoRA SFT on DeepSeek-R1-0528-Qwen3-8B.

Assistant-only loss masking via the tokenizer's own chat template. Uses
transformers.Trainer + peft directly (no TRL) so the script does not depend
on TRL's shifting SFTTrainer signature.

Run:  python train_lora.py --base /weights/base/DeepSeek-R1-0528-Qwen3-8B
"""
import argparse, json, os, math
import torch
from torch.utils.data import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                          TrainingArguments, BitsAndBytesConfig)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

IGNORE = -100


class ChatSFT(Dataset):
    """Tokenise messages; mask every token that is not assistant content."""

    def __init__(self, path, tok, max_len=2048):
        self.rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        self.tok, self.max_len = tok, max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        msgs = self.rows[i]["messages"]
        # prompt = everything up to (and including) the assistant generation prefix
        prompt_ids = self.tok.apply_chat_template(
            msgs[:-1], tokenize=True, add_generation_prompt=True)
        full_ids = self.tok.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=False)
        # guard: template must be prefix-consistent, else fall back to answer-only
        if full_ids[:len(prompt_ids)] != prompt_ids:
            ans = self.tok(msgs[-1]["content"], add_special_tokens=False)["input_ids"]
            full_ids = prompt_ids + ans
        labels = [IGNORE] * len(prompt_ids) + full_ids[len(prompt_ids):]
        full_ids, labels = full_ids[:self.max_len], labels[:self.max_len]
        return {"input_ids": full_ids, "labels": labels}


def collate(batch, pad_id):
    n = max(len(b["input_ids"]) for b in batch)
    out = {"input_ids": [], "labels": [], "attention_mask": []}
    for b in batch:
        k = n - len(b["input_ids"])
        out["input_ids"].append(b["input_ids"] + [pad_id] * k)
        out["labels"].append(b["labels"] + [IGNORE] * k)
        out["attention_mask"].append([1] * len(b["input_ids"]) + [0] * k)
    return {k: torch.tensor(v, dtype=torch.long) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--train", default="sft_train.jsonl")
    ap.add_argument("--val", default="sft_val.jsonl")
    ap.add_argument("--out", default="out/adapter")
    ap.add_argument("--max_len", type=int, default=2048)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--bsz", type=int, default=4)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora_r", type=int, default=32)
    ap.add_argument("--lora_alpha", type=int, default=64)
    ap.add_argument("--load_4bit", action="store_true", help="QLoRA path for 15GB GPUs")
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    bf16 = torch.cuda.is_bf16_supported()
    kw = dict(trust_remote_code=True, attn_implementation="sdpa",
              dtype=torch.bfloat16 if bf16 else torch.float16)
    if a.load_4bit:
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16 if bf16 else torch.float16,
            bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(a.base, **kw)
    if a.load_4bit:
        model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    model = get_peft_model(model, LoraConfig(
        r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    ds_tr, ds_va = ChatSFT(a.train, tok, a.max_len), ChatSFT(a.val, tok, a.max_len)
    print(f"train={len(ds_tr)} val={len(ds_va)} bf16={bf16} 4bit={a.load_4bit}", flush=True)

    args = TrainingArguments(
        output_dir="out/ckpt",
        num_train_epochs=a.epochs,
        per_device_train_batch_size=a.bsz,
        per_device_eval_batch_size=a.bsz,
        gradient_accumulation_steps=a.accum,
        learning_rate=a.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=100,
        save_strategy="steps",
        save_steps=200,
        save_total_limit=2,
        bf16=bf16, fp16=not bf16,
        optim="paged_adamw_8bit" if a.load_4bit else "adamw_torch",
        gradient_checkpointing=True,
        report_to=[],
    )
    trainer = Trainer(model=model, args=args, train_dataset=ds_tr, eval_dataset=ds_va,
                      data_collator=lambda b: collate(b, tok.pad_token_id))
    trainer.train()

    os.makedirs(a.out, exist_ok=True)
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)

    hist = [h for h in trainer.state.log_history]
    json.dump(hist, open("out/log_history.json", "w"), ensure_ascii=False, indent=2)
    final = [h for h in hist if "eval_loss" in h]
    if final:
        json.dump({"final_eval_loss": final[-1]["eval_loss"],
                   "final_eval_ppl": math.exp(min(final[-1]["eval_loss"], 20))},
                  open("out/train_summary.json", "w"), indent=2)
    print("SAVED", a.out, flush=True)


if __name__ == "__main__":
    main()
