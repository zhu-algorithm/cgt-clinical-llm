"""药学类医疗服务价格项目运行评估。

政策依据
--------
《药学类医疗服务价格项目立项指南（试行）》，国家医疗保障局，2026-02-12 印发，
将已有价格项目规范整合为 10 项，按应用场景设 4 类：
药学诊查、集中配置、浓度测定、中药药学服务。
省级制定全省统一价格基准，由具有价格管理权限的统筹地区上下浮动确定执行价格。

⚠ 本模块的评估方向与医保控费**相反**，这一点必须先讲清楚：

    药学类价格项目是**新设的、被鼓励的**项目，立项本意是补偿药师劳务、
    推动以药养医向以技养医转变。因此：

    · 开展率低**不等于违规**——可能是定价过低、药师人力不足、
      医院未配置该服务，甚至是医院不知道可以收费。
    · 把运行评估做成拒付工具，会直接扼杀政策本意。

因此评估必须**双向**：既查「只收费不服务」（虚收，走稽核），
也查「服务了不敢收费」（漏收，走政策反馈）。只做前者就是把评估做反了。
"""
from __future__ import annotations
from dataclasses import dataclass, field

# 4 类（《立项指南（试行）》应用场景分类）
PRICE_ITEM_CLASSES = {
    "药学诊查": "药师面向患者的诊查类服务，产出为用药评估与干预意见",
    "集中配置": "静脉用药等集中调配，产出为成品输液与调配记录",
    "浓度测定": "治疗药物监测，产出为血药浓度结果与剂量调整建议",
    "中药药学服务": "中药饮片与制剂相关的药学服务",
}

# 解读辅导的效力边界——这条直接约束规则引擎
INTERPRETATION_NOT_AUDIT_BASIS = (
    "立项指南解读辅导是立项指南层面的解释解读口径，"
    "不直接作为医保基金稽核依据；医疗机构收费以本地区医保部门出台的"
    "医疗服务价格政策为准。因此规则引擎不得以解读辅导条目作为拒付依据。"
)


class EvaluationMisuse(Exception):
    """评估结果被用于其设计目的之外的用途。"""


@dataclass
class ServiceRecord:
    """一次药学服务的记录与收费状态。"""
    record_id: str
    item_class: str
    item_name: str
    institution: str
    pharmacist: str
    service_time: str
    has_work_record: bool      # 是否存在合规工作记录
    billed: bool               # 是否已收费
    price: float = 0.0
    region: str = ""


def coverage(records, institutions) -> dict:
    """开展率：多少机构真的在提供这项服务。

    这是**政策效果指标**，不是违规指标。低开展率指向定价或人力，
    不指向违规。
    """
    by_item = {}
    for r in records:
        by_item.setdefault(r.item_name, set()).add(r.institution)
    n = len(institutions) or 1
    out = [dict(项目=k, 开展机构数=len(v), 开展率=round(len(v) / n, 4))
           for k, v in sorted(by_item.items())]
    未开展 = sorted(set(PRICE_ITEM_CLASSES) -
                    {r.item_class for r in records})
    return dict(机构总数=len(institutions), 分项目=out, 未见开展的类别=未开展,
                说明="开展率低不等于违规——须先排查定价水平、药师配置与政策知晓度，"
                     "不得据此发起稽核")


def underbilling(records) -> dict:
    """漏收：有合规工作记录，却没有收费。

    这是**政策落地障碍**，输出方向是价格主管部门与医院管理层，
    不是稽核线索。药师做了事没收到钱，是立项指南要解决的问题本身。
    """
    hits = [r for r in records if r.has_work_record and not r.billed]
    by_inst = {}
    for r in hits:
        by_inst[r.institution] = by_inst.get(r.institution, 0) + 1
    return dict(漏收笔数=len(hits),
                涉及机构=dict(sorted(by_inst.items(), key=lambda x: -x[1])),
                去向="价格主管部门与医院管理层",
                说明="漏收反映政策知晓度或流程缺失，是立项指南要解决的问题本身；"
                     "不得作为稽核线索，也不得计入任何「挽回金额」")


def phantom_billing(records) -> dict:
    """虚收：有收费，但没有合规工作记录。

    这一条**才是**稽核方向——收了费而服务未发生，与长护险虚构服务同构。
    但输出的是**待核线索**，不是违规结论：工作记录缺失也可能是
    记录系统未对接，须经人工核实。
    """
    hits = [r for r in records if r.billed and not r.has_work_record]
    amount = round(sum(r.price for r in hits), 2)
    by_inst = {}
    for r in hits:
        by_inst.setdefault(r.institution, []).append(r.record_id)
    return dict(待核笔数=len(hits), 待核金额=amount,
                分机构={k: len(v) for k, v in sorted(
                    by_inst.items(), key=lambda x: -len(x[1]))},
                判定="待核线索，非违规结论",
                说明="工作记录缺失也可能源于记录系统未对接；须人工核实后定性。"
                     "待核金额不得对外表述为「查出的违规金额」")


def price_dispersion(records) -> dict:
    """统筹区间价格离散度。

    价格由统筹地区在省级基准上下浮动确定，**离散本身是制度设计的结果**，
    不是异常。本函数只描述分布，供价格主管部门参考，不产出任何判定。
    """
    by_item = {}
    for r in records:
        if r.billed and r.price > 0:
            by_item.setdefault((r.item_name, r.region), []).append(r.price)
    rows = []
    for (item, region), ps in sorted(by_item.items()):
        rows.append(dict(项目=item, 统筹区=region, 样本数=len(ps),
                         均价=round(sum(ps) / len(ps), 2),
                         最低=min(ps), 最高=max(ps)))
    return dict(分布=rows,
                说明="价格由统筹地区在省级基准上下浮动确定，离散是制度设计的结果，"
                     "不是异常信号；本函数不产出判定")


def assert_not_suppression(purpose: str) -> None:
    """红线：运行评估的结论不得直接转为拒付或压缩服务的依据。"""
    BANNED = ("拒付", "扣款", "追回", "限制开展", "压缩", "核减", "暂停项目")
    hit = [b for b in BANNED if b in purpose]
    if hit:
        raise EvaluationMisuse(
            f"用途声明含「{hit}」——运行评估不得用于此目的。"
            f"药学类价格项目是被鼓励的新设项目，评估目的是发现落地障碍，"
            f"把它做成拒付工具会直接扼杀政策本意。"
            f"虚收线索请走 phantom_billing() 的独立稽核通道。")
    return None


def report(records, institutions) -> dict:
    return dict(
        四类=list(PRICE_ITEM_CLASSES),
        开展=coverage(records, institutions),
        漏收=underbilling(records),
        虚收待核=phantom_billing(records),
        价格分布=price_dispersion(records),
        效力边界=INTERPRETATION_NOT_AUDIT_BASIS)
