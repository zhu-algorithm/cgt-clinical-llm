"""分级受理：补数据与改流程不能走同一个通道。

能改流程的一方，就能关掉风险护盾的 fail-open、拆掉编码回补的硬凭据闸门、
翻转误拒代价的不对称。这套系统在做影响患者的支付判定，
因此接入口按「能造成多大伤害」分级，而不是按「改动多大」分级。
"""
from __future__ import annotations

TIERS = {
    "T0": dict(
        名称="数据补充",
        范围="法规条文、知识点、去标识化案例",
        机械校验="结构 + 来源可溯 + PHI 拒绝",
        人工同意="仓库所有者合并 PR",
        说明="法规条文本身是公开的，补充它不改变任何判定逻辑"),
    "T1": dict(
        名称="规则新增或修改",
        范围="检出规则、阈值、适用范围",
        机械校验="T0 全部 + 不变量测试 + 影子运行比对",
        人工同意="仓库所有者合并 PR + 必须附误报代价说明",
        说明="规则改动会改变谁被拒付，因此必须先在影子模式跑出"
             "误报率变化，不能直接生效"),
    "T2": dict(
        名称="流程变更",
        范围="模块决策顺序、判定优先级、新增判定环节",
        机械校验="T1 全部 + 受保护不变量逐条复核 + 回滚方案校验",
        人工同意="仓库所有者以**自己的密钥签名批准**，而非仅点击合并",
        说明="合并按钮会被误点；流程变更要求所有者主动签名，"
             "留下不可否认的授权记录"),
    "T3": dict(
        名称="受保护不变量",
        范围="安全红线本身（见 invariants.py 的 PROTECTED）",
        机械校验="——",
        人工同意="**本通道不受理**",
        说明="红线不能由贡献通道修改。外部智能体若能改红线，"
             "分级就失去意义。所有者如确需修改，直接在本地改并自行承担，"
             "不经过这个自动化入口"),
}


def classify(kind: str, touched_paths) -> str:
    """按贡献类型与改动路径定级，取最高级。"""
    from .invariants import PROTECTED
    paths = [str(p) for p in touched_paths]
    if any(any(p.endswith(pf) for pf in PROTECTED) for p in paths):
        return "T3"
    if kind == "流程变更":
        return "T2"
    if kind == "规则":
        return "T1"
    return "T0"


class TierRefused(Exception):
    """该级别不由本通道受理。"""


def admit(kind: str, touched_paths, declared_tier: str) -> dict:
    tier = classify(kind, touched_paths)
    if tier == "T3":
        raise TierRefused(
            "改动触及受保护不变量文件，本通道不受理。"
            "红线不能由贡献通道修改——否则分级形同虚设。")
    if declared_tier != tier:
        raise TierRefused(
            f"自报级别 {declared_tier} 与实际定级 {tier} 不符。"
            f"按实际定级 {tier} 处理，请重新提交并说明。")
    return dict(级别=tier, **TIERS[tier])
