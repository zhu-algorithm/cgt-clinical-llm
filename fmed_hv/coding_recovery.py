"""编码回补（Revenue Recovery）—— 医院价值栈的入口钩子。

做什么：从病案首页、手术记录、医嘱、检验检查结果中抽取**实际发生**的诊疗强度，
与所填报的 DRG/DIP 病组比对，识别填报低于实际的病例。

为什么不违规：DRG 2.0 分组方案的政策目标被官方表述为
「让应该在 A 组的病例就在 A 组」——既反对高套，也反对低编。
本模块**拒绝高套**（细则已明确可认定为造成基金损失），只纠正低编。

⚠ 安全属性：本模块距离「高套工具」只有一步之遥。防线只有一条——

    每条建议必须引用病历中证明该并发症/操作**真实发生**的原始凭据；
    无凭据不建议，哪怕分组更高。

    且凭据不得是病历的叙述性文字本身（与 L2 同类的循环论证问题）：
    并发症须有检验/影像/用药佐证，手术操作须有手术记录与麻醉记录。
"""
from __future__ import annotations
from dataclasses import dataclass, field

# 可作为「真实发生」佐证的凭据类型。叙述性文字不在其中。
HARD_EVIDENCE = {
    "手术记录", "麻醉记录", "检验报告", "影像报告", "病理报告",
    "医嘱明细", "护理记录", "输血记录", "耗材使用记录",
}
NARRATIVE_ONLY = {"入院记录叙述", "出院小结叙述", "首页诊断文字"}


class UpcodingRefused(Exception):
    """建议会抬高分组但无硬凭据支撑——拒绝输出。"""


@dataclass
class Evidence:
    kind: str
    locator: str          # 可回溯定位：记录号/报告号/医嘱号
    excerpt: str = ""


@dataclass
class CodingGap:
    case_id: str
    filed_group: str
    filed_payment: float
    candidate_group: str
    candidate_payment: float
    item_type: str        # 并发症 | 合并症 | 手术操作
    item_name: str
    evidences: list = field(default_factory=list)

    @property
    def delta(self) -> float:
        return round(self.candidate_payment - self.filed_payment, 2)

    @property
    def hard_evidence(self):
        return [e for e in self.evidences if e.kind in HARD_EVIDENCE]


def assess(gap: CodingGap) -> dict:
    """评估一条回补建议。无硬凭据则拒绝输出，而非降级为「待确认」。"""
    if gap.delta <= 0:
        return dict(case_id=gap.case_id, 判定="非低编", 说明="候选分组支付不高于已填报分组")
    hard = gap.hard_evidence
    if not hard:
        srcs = sorted({e.kind for e in gap.evidences}) or ["无"]
        raise UpcodingRefused(
            f"病例 {gap.case_id}：建议将「{gap.item_name}」计入分组可增加支付 "
            f"{gap.delta} 元，但现有凭据仅为 {srcs}，无硬凭据支撑。"
            f"叙述性文字不能证明并发症/操作真实发生——"
            f"在无凭据情况下输出该建议即构成高套工具。拒绝输出。")
    return dict(
        case_id=gap.case_id, 判定="低编可回补",
        项目类型=gap.item_type, 项目=gap.item_name,
        已填报分组=gap.filed_group, 建议分组=gap.candidate_group,
        可回补金额=gap.delta,
        凭据=[dict(类型=e.kind, 定位=e.locator) for e in hard],
        说明="凭据证明该项真实发生；填报修正后分组上升属纠正低编，非高套")


def batch(gaps):
    """批量评估。被拒绝的建议单独列示，不混入可回补清单。"""
    ok, refused, nonlow = [], [], []
    for g in gaps:
        try:
            r = assess(g)
        except UpcodingRefused as e:
            refused.append(dict(case_id=g.case_id, 项目=g.item_name,
                                潜在金额=g.delta, 拒绝原因=str(e)[:160]))
            continue
        (ok if r["判定"] == "低编可回补" else nonlow).append(r)
    return dict(
        可回补=ok, 可回补笔数=len(ok),
        可回补金额合计=round(sum(x["可回补金额"] for x in ok), 2),
        因无硬凭据被拒=refused, 被拒笔数=len(refused),
        被拒潜在金额=round(sum(x["潜在金额"] for x in refused), 2),
        非低编=len(nonlow),
        说明="「被拒潜在金额」不得计入医院收益测算——它恰恰是本模块主动放弃的部分，"
             "也是本模块与高套工具的分界线")


def audit_direction(gaps):
    """方向性审计：本模块是否只向上纠正？

    若出现「建议分组支付低于已填报」的情形，说明存在高套，
    应转入 DRG 高套检出链路（付方侧），不属本模块职责。
    """
    up = [g for g in gaps if g.delta > 0]
    down = [g for g in gaps if g.delta < 0]
    return dict(向上纠正=len(up), 疑似高套=len(down),
                处置=("无" if not down else
                      f"{len(down)} 例候选分组低于已填报，疑似高套，"
                      f"须转付方侧 DRG 高套检出，本模块不输出建议"))
