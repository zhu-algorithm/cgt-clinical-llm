"""申诉引擎（Appeal Engine）—— 把申诉从「写情况说明」变成「提交证据链」。

被拒付时自动生成带条文级溯源的申诉材料：引用的规则原文、适用判定的客观证据
（LIS 数值、影像结论、历史结算记录）、以及同类病例的同伴分布位置。

⚠ 一条反游戏约束：

  申诉改判率同时是**模型质量考核指标**。若本引擎对「拒付正确」的病例
  也批量生成申诉，改判率会被稀释，而模型质量看起来变好——
  这是用医院的申诉成本去粉饰付方指标。

  因此本引擎**拒绝为证据不支持的拒付生成申诉**，并把这类病例
  单独列为「拒付成立」返还医院，而不是生成一份注定被驳回的材料。
"""
from __future__ import annotations
from dataclasses import dataclass, field


class AppealRefused(Exception):
    """证据不支持申诉——拒绝生成材料。"""


@dataclass
class Denial:
    case_id: str
    denied_amount: float
    rule_id: str
    rule_text: str
    rule_version: str          # as-of：拒付所依据条文的生效版本
    service_date: str
    objective_evidence: list = field(default_factory=list)  # [(来源, 值, 定位)]
    peer_percentile: float | None = None   # 该病例在同伴分布中的位次


def build_appeal(d: Denial, l2_status: str) -> dict:
    """生成申诉材料。l2_status 为 L2 事实核验结论。

    L2 为 CONTRADICTED 时拒绝申诉——客观证据与院方陈述矛盾，
    此时生成申诉是在为造假背书。
    """
    if l2_status == "CONTRADICTED":
        raise AppealRefused(
            f"病例 {d.case_id}：L2 事实核验为 CONTRADICTED（客观证据与陈述矛盾），"
            f"拒付成立。为此类病例生成申诉材料等于为造假背书，拒绝输出。")
    if not d.objective_evidence:
        raise AppealRefused(
            f"病例 {d.case_id}：无客观证据可引（仅病历自述）。"
            f"生成一份注定被驳回的材料只会消耗医院申诉成本，"
            f"并稀释申诉改判率这一模型质量指标。拒绝输出。")
    return dict(
        case_id=d.case_id, 申诉金额=d.denied_amount,
        一_所依据条文=dict(规则号=d.rule_id, 原文=d.rule_text,
                          生效版本=d.rule_version, 费用发生日=d.service_date,
                          说明="按 as-of 原则引用费用发生时点生效的版本"),
        二_客观证据=[dict(来源=s, 取值=v, 定位=loc)
                     for s, v, loc in d.objective_evidence],
        三_同伴分布位次=(f"本例处于同病组第 {d.peer_percentile:.0%} 分位"
                        if d.peer_percentile is not None else "未取得"),
        四_L2核验结论=l2_status,
        申诉要点="客观证据支持该项符合支付范围限定；请复核拒付决定")


def batch(denials, l2_map):
    ok, refused = [], []
    for d in denials:
        try:
            ok.append(build_appeal(d, l2_map.get(d.case_id, "UNVERIFIED")))
        except AppealRefused as e:
            refused.append(dict(case_id=d.case_id, 金额=d.denied_amount,
                                结论="拒付成立-不申诉", 原因=str(e)[:130]))
    return dict(可申诉=len(ok), 可申诉金额=round(sum(x["申诉金额"] for x in ok), 2),
                拒付成立=len(refused),
                拒付成立金额=round(sum(x["金额"] for x in refused), 2),
                材料=ok[:5], 不申诉清单=refused,
                说明="「拒付成立」部分如实返还医院，不生成注定被驳回的材料——"
                     "既省医院申诉成本，也不稀释申诉改判率这一付方质量指标")


def quality_signal(appeal_results):
    """申诉改判率作为模型质量信号。高改判率 = 模型误拒多。"""
    n = len(appeal_results) or 1
    overturned = [a for a in appeal_results if a.get("改判")]
    return dict(已裁决申诉=len(appeal_results), 改判数=len(overturned),
                改判率=round(len(overturned)/n, 4),
                读法="改判率高说明模型误拒多，应回查误拒代价分层与证据门槛；"
                     "**不得通过减少申诉来改善该指标**")
