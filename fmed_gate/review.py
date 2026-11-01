"""CI 受理入口：验签 → 定级 → 内容校验 → 不变量。任一失败即阻断合并。

CI **不持有解密私钥**，因此只验证信封与结构，不读取加密内容。
真正的同意发生在仓库所有者合并 PR 的那一刻；CI 的作用是
保证一次疲劳的点击也不会突破红线。
"""
from __future__ import annotations
import json, pathlib, sys

from .envelope import verify, SignatureInvalid
from .schema import validate, ContributionRejected
from .tiers import admit, TierRefused
from .invariants import assert_ok, InvariantViolation


def load_trusted(root=".") -> dict:
    p = pathlib.Path(root) / "gate" / "trusted_keys.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    # 以 _ 开头的键是文档注释，不是密钥条目
    return {k: v["public_key"] for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)}


def review(env: dict, touched_paths, root=".") -> dict:
    steps = []
    body = verify(env, load_trusted(root))
    steps.append(f"验签通过 · {body['agent_id']}")
    t = admit(body["payload"].get("类型", ""), touched_paths, body["tier"])
    steps.append(f"定级 {t['级别']}（{t['名称']}）· 同意方式：{t['人工同意']}")
    v = validate(body["payload"]["类型"], body["payload"]["内容"])
    steps.append(f"内容校验通过 · {v['字段数']} 字段")
    inv = assert_ok(root)
    steps.append(f"不变量 {inv['已检查']} 项全部保持")
    return dict(受理="是", 级别=t["级别"], 步骤=steps,
                下一步=t["人工同意"],
                提示="CI 通过不等于已获同意——合并权在仓库所有者")


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        r = assert_ok(".")
        print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
    env = json.loads(pathlib.Path(argv[0]).read_text(encoding="utf-8"))
    touched = argv[1:]
    try:
        print(json.dumps(review(env, touched), ensure_ascii=False, indent=2))
        return 0
    except (SignatureInvalid, ContributionRejected,
            TierRefused, InvariantViolation) as e:
        print(f"::error::{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
