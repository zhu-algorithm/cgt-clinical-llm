"""Build the ZhaoYou-Med hepatobiliary-weighted Chinese SFT mixture.

Sources (all Apache-2.0, no GPT-generated content):
  - FreedomIntelligence/Huatuo26M-Lite      real patient consultations, department-labelled
  - fzkuji/CMExam                           real Chinese medical licensing exam items + explanations
  - FreedomIntelligence/huatuo_knowledge_graph_qa   KG-derived factual QA
"""
import json, re, random, hashlib, collections, pathlib

random.seed(20261024)

HEP_KW = re.compile(r"肝|胆囊|胆管|胆道|胰腺|门静脉|黄疸|乙型肝炎|丙肝|肝硬化|腹水|脾功能亢进|Child|HBV|HCV")

# promotional / identifying junk common in scraped Chinese medical Q&A
AD_PAT = re.compile(
    r"(咨询电话|联系电话|微信|QQ|公众号|点击|免费咨询|专家热线|在线咨询|http[s]?://|www\.|"
    r"祝您健康|祝你健康|祝早日康复|希望我的回答|以上是我的回答|仅供参考，具体请咨询)"
)
PHONE = re.compile(r"\d{3,4}-?\d{7,8}|\b1[3-9]\d{9}\b")

SYSTEM = (
    "你是钊猷医脉（ZhaoYou-Med），一个中文医疗咨询助手，肝胆方向为重点。"
    "回答须：先归纳患者关键信息，再给出可能的诊断方向与依据，最后给出分级建议。"
    "严禁给出具体处方剂量；涉及急症征象时必须首先提示立即就医；"
    "你提供的是医学信息参考，不构成诊疗决策，最终诊疗须由具备资质的医师结合完整病情作出。"
)


def norm(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    t = PHONE.sub("", t)
    return t


def clean_ok(q, a, qmin=12, qmax=600, amin=30, amax=1200):
    if not (qmin <= len(q) <= qmax and amin <= len(a) <= amax):
        return False
    if AD_PAT.search(a) or AD_PAT.search(q):
        return False
    return True


def key(q):
    return hashlib.md5(re.sub(r"[^\u4e00-\u9fff]", "", q)[:60].encode()).hexdigest()


def build(paths, out_dir="data", n_target=20000, eval_hep_n=600, eval_gen_n=600):
    out = pathlib.Path(out_dir); out.mkdir(exist_ok=True)
    seen, recs = set(), []

    def add(q, a, src, dept, hep):
        q, a = norm(q), norm(a)
        if not clean_ok(q, a):
            return False
        k = key(q)
        if k in seen:
            return False
        seen.add(k)
        recs.append({
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": q},
                         {"role": "assistant", "content": a}],
            "source": src, "department": dept, "hepatobiliary": hep,
        })
        return True

    # ---- 1. consultations -------------------------------------------------
    h26 = [json.loads(l) for l in open(paths["h26"], encoding="utf-8")]
    h26 = [r for r in h26 if (r.get("score") or 0) >= 4]          # keep best-rated answers
    hep_rows, gen_rows = [], []
    for r in h26:
        blob = f"{r.get('related_diseases','')}{r.get('question','')}"
        (hep_rows if HEP_KW.search(blob) else gen_rows).append(r)
    random.shuffle(hep_rows); random.shuffle(gen_rows)

    # hold out hepatobiliary consultations for eval BEFORE training sampling
    eval_hep = hep_rows[:eval_hep_n]
    hep_rows = hep_rows[eval_hep_n:]

    for r in hep_rows[:int(n_target * 0.40)]:
        add(r["question"], r["answer"], "huatuo26m", r.get("label"), True)
    # department-balanced general slice guards against catastrophic forgetting
    by_dept = collections.defaultdict(list)
    for r in gen_rows:
        by_dept[r.get("label")].append(r)
    quota = int(n_target * 0.30) // max(len(by_dept), 1)
    for dept, rows in by_dept.items():
        for r in rows[:quota]:
            add(r["question"], r["answer"], "huatuo26m", dept, False)

    # ---- 2. exam items with explanations ----------------------------------
    cm = []
    for split in ("train", "valid", "test"):
        with open(paths[f"cmexam_{split}"], encoding="utf-8") as f:
            cm += [json.loads(l) for l in f if l.strip()]
    cm_hep = [r for r in cm if HEP_KW.search(r["Question"] + str(r.get("Explanation", "")))]
    cm_gen = [r for r in cm if r not in cm_hep] if False else [r for r in cm if not HEP_KW.search(r["Question"] + str(r.get("Explanation", "")))]
    random.shuffle(cm_hep); random.shuffle(cm_gen)

    eval_cm_hep = cm_hep[:eval_hep_n]           # hepatobiliary eval items
    eval_cm_gen = cm_gen[:eval_gen_n]           # general-medicine eval items
    cm_hep, cm_gen = cm_hep[eval_hep_n:], cm_gen[eval_gen_n:]

    def exam_to_sft(r):
        opts = "\n".join(f"{o['key']}. {o['value']}" for o in r["Options"])
        q = f"{r['Question']}\n{opts}"
        a = f"答案：{r['Answer']}\n\n解析：{r.get('Explanation','')}"
        return q, a
    for r in cm_hep[:int(n_target * 0.15)]:
        q, a = exam_to_sft(r); add(q, a, "cmexam", None, True)
    for r in cm_gen[:int(n_target * 0.10)]:
        q, a = exam_to_sft(r); add(q, a, "cmexam", None, False)

    # ---- 3. KG factual QA --------------------------------------------------
    kg = []
    with open(paths["kg"], encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            qs, ans = r.get("questions") or [], r.get("answers") or []
            if qs and ans and HEP_KW.search(qs[0]):
                kg.append((qs[0], ans[0]))
    random.shuffle(kg)
    for q, a in kg[:int(n_target * 0.05)]:
        add(q, a, "huatuo_kg", None, True)

    # ---- write -------------------------------------------------------------
    random.shuffle(recs)
    n_val = max(400, int(len(recs) * 0.02))
    val, train = recs[:n_val], recs[n_val:]
    for name, rows in (("train", train), ("val", val)):
        with open(out / f"sft_{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(out / "eval_cmexam_hepato.jsonl", "w", encoding="utf-8") as f:
        for r in eval_cm_hep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "eval_cmexam_general.jsonl", "w", encoding="utf-8") as f:
        for r in eval_cm_gen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "eval_consult_hepato.jsonl", "w", encoding="utf-8") as f:
        for r in eval_hep:
            f.write(json.dumps({"question": norm(r["question"]),
                                "reference": norm(r["answer"]),
                                "department": r.get("label"),
                                "related_diseases": r.get("related_diseases")},
                               ensure_ascii=False) + "\n")

    stats = {
        "train": len(train), "val": len(val),
        "train_hepatobiliary": sum(r["hepatobiliary"] for r in train),
        "by_source": dict(collections.Counter(r["source"] for r in train)),
        "by_department": dict(collections.Counter(r["department"] for r in train if r["department"])),
        "eval_cmexam_hepato": len(eval_cm_hep),
        "eval_cmexam_general": len(eval_cm_gen),
        "eval_consult_hepato": len(eval_hep),
    }
    json.dump(stats, open(out / "dataset_stats.json", "w"), ensure_ascii=False, indent=2)
    return stats
