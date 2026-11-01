"""贡献内容校验：结构、来源可溯、以及患者级数据的硬拒绝。"""
from __future__ import annotations
import re

# 贡献类型 → 必填字段
REQUIRED = {
    "法规条文": {"标题", "发文机关", "文号", "施行日期", "条文号", "条文原文", "来源URL"},
    "知识点":   {"名称", "判定逻辑", "逻辑依据", "来源", "适用版本"},
    "规则":     {"规则ID", "触发条件", "处置建议", "依据条文", "误报代价说明"},
    "流程变更": {"目标模块", "变更说明", "风险评估", "不变量影响", "回滚方案"},
}

# 患者级标识——出现即拒绝。密文同样拒绝：公开仓库里的密文是永久的。
PHI_PATTERNS = [
    (r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "身份证号"),
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"(病案号|住院号|就诊号|社保卡号|医保卡号)\s*[:：]?\s*\w{4,}", "就医标识"),
    (r"\b\d{4}-\d{2}-\d{2}\b.{0,12}(入院|出院|就诊)", "就诊日期"),
]


class ContributionRejected(Exception):
    """内容不受理。"""


def check_phi(payload) -> None:
    """患者级数据一律拒绝进入公开仓库。

    这不是「加密后就可以」——密文提交即永久留存，而密钥会泄露；
    真实世界数据必须走线下授权通道与数据使用审批，不走代码仓库。
    """
    blob = str(payload)
    hits = sorted({label for pat, label in PHI_PATTERNS if re.search(pat, blob)})
    if hits:
        raise ContributionRejected(
            f"检出疑似患者级标识：{hits}。公开仓库不接收真实世界个案数据——"
            f"加密也不行：密文永久留存而密钥会泄露。"
            f"请改用去标识化示例，真实数据走线下数据使用审批通道。")


def check_structure(kind: str, payload: dict) -> None:
    if kind not in REQUIRED:
        raise ContributionRejected(
            f"未知贡献类型「{kind}」，可选：{sorted(REQUIRED)}")
    missing = REQUIRED[kind] - set(payload)
    if missing:
        raise ContributionRejected(f"「{kind}」缺少必填字段：{sorted(missing)}")


def check_traceable(kind: str, payload: dict) -> None:
    """每条判定都要有据——来源不可是「模型生成」或「综合整理」。"""
    BANNED = ("模型生成", "AI生成", "综合整理", "根据经验", "业内通行",
              "大模型输出", "推测", "据了解")
    src = str(payload.get("来源URL") or payload.get("来源")
              or payload.get("依据条文") or payload.get("逻辑依据") or "")
    if not src.strip():
        raise ContributionRejected("缺少来源：智能体的每条判定都必须可追溯到具体出处")
    for b in BANNED:
        if b in src:
            raise ContributionRejected(
                f"来源标注为「{b}」——不可作为判定依据。"
                f"法规类须给出发文机关＋文号＋条文号；知识点须给出可核验出处。")


def validate(kind: str, payload: dict) -> dict:
    check_phi(payload)
    check_structure(kind, payload)
    check_traceable(kind, payload)
    return dict(类型=kind, 校验="通过", 字段数=len(payload))
