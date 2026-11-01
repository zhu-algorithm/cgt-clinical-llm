"""受保护不变量：CI 在每个 PR 上执行，失败即阻断合并。

这些是整个方案里「改了就不再是同一个系统」的性质。
它们以**可执行检查**存在，而不是写在文档里——文档不会在 CI 里失败。
"""
from __future__ import annotations
import pathlib, re

# 触及这些文件的贡献，本通道一律不受理（T3）
PROTECTED = (
    "fmed_gate/invariants.py",
    "fmed_gate/tiers.py",
    "fmed_rx/scope_guard.py",
    "fmed_rx/verification.py",
    ".github/workflows/gate.yml",
    "gate/trusted_keys.json",
)

# (文件, 必须存在的模式, 失败说明)
CHECKS = [
    ("fmed_hv/risk_shield.py", r"wait=False",
     "风险护盾必须以 shutdown(wait=False) 关闭线程池；"
     "wait=True 会把被弃用的慢检查等完，fail-open 形同虚设"),
    ("fmed_hv/risk_shield.py", r"级别=\"通过\".*?超时=True",
     "SLA 超时必须放行（fail-open）：不得因系统响应慢而阻断诊疗"),
    ("fmed_hv/coding_recovery.py", r"HARD_EVIDENCE",
     "编码回补必须保留硬凭据闸门；无凭据即输出建议就是高套工具"),
    ("fmed_hv/coding_recovery.py", r"叙述",
     "叙述性文字不得作为并发症/操作真实发生的凭据"),
    ("fmed_hv/appeal_engine.py", r"CONTRADICTED",
     "申诉引擎必须拒绝为 L2 判 CONTRADICTED 的病例生成材料——那是为造假背书"),
    ("fmed_hv/drg_api.py", r"FORBIDDEN_FLOWS",
     "DRG 接口必须保留禁止流向：拒付信号不得进入卫健委绩效考核"),
    ("fmed_hv/drg_api.py", r"ScopeDenied",
     "越权访问必须硬失败，不得降级为告警"),
    ("fmed_hv/drg_ops.py", r"dumping_watch|推诿",
     "DRG 运营分析必须保留推诿监测；盈亏分析不得脱离质量配重"),
]

# 全仓库禁止出现的表述
BANNED_GLOBAL = [
    (r"按节省额分成|节省额分成|分成比例\s*[:：]\s*\d",
     "计费不得与节省额挂钩——分成会制造把模型训成拒付偏向的激励"),
    (r"拒付率.*(KPI|考核指标|目标值)",
     "拒付率是供方侧指标，不得作为付方 KPI"),
    (r"2,?313|1,?245(?!\.85)",
     "支付资格暂停/终止人次为公开检索未取得项，不得填数"),
]


class InvariantViolation(Exception):
    pass


def run(root=".") -> dict:
    root = pathlib.Path(root)
    failures, checked = [], 0
    for rel, pat, why in CHECKS:
        f = root / rel
        if not f.exists():
            failures.append(dict(文件=rel, 问题="文件缺失", 说明=why)); continue
        checked += 1
        if not re.search(pat, f.read_text(encoding="utf-8"), re.S):
            failures.append(dict(文件=rel, 问题=f"不变量丢失：{pat}", 说明=why))
    for f in root.rglob("*.py"):
        if "fmed_gate/invariants.py" in str(f): continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for pat, why in BANNED_GLOBAL:
            if re.search(pat, txt):
                failures.append(dict(文件=str(f.relative_to(root)),
                                     问题=f"禁止表述：{pat}", 说明=why))
    return dict(已检查=checked, 失败数=len(failures), 失败=failures,
                结论="通过" if not failures else "阻断合并")


def assert_ok(root="."):
    r = run(root)
    if r["失败数"]:
        raise InvariantViolation(
            f"{r['失败数']} 项受保护不变量被破坏：\n" +
            "\n".join(f"  · {x['文件']}：{x['说明']}" for x in r["失败"]))
    return r
