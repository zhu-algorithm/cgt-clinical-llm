"""DRG 双方接口 —— 按用途分区，而非给两家同一个数据出口。

卫健委与医保局对 DRG 数据有**不同的法定用途**：
  医保局 → 支付正确性：分组准确性、高套/低编、法定损失计算、拒付与追回
  卫健委 → 医疗质量：CMI、四级手术占比、低风险组死亡率、时间/费用消耗指数

同一份底层数据，**允许输出的字段必须不同**。

⚠ 核心治理约束（本模块存在的首要理由）：

    **医保局的拒付与违规信号，不得流入卫健委的绩效考核。**

    否则就把「拒付 → 记分 → 绩效 → 职称」叠成一条链，
    而那正是让医院铁了心要让系统失效的机制（见「单向系统的死循环」）。
    一个把两家出口合并的接口，技术上省事，治理上是灾难。

反向约束同样成立：卫健委的质量评分不得回流医保局用作拒付依据——
质量差不是违规，不构成不予支付的法定理由。
"""
from __future__ import annotations

PAYER = "医保局"          # 医疗保障行政部门/经办机构
HEALTH = "卫健委"         # 卫生健康行政部门
HOSPITAL = "医院"         # 被监管/被服务方自身

# 字段 → 允许访问的主体。未列入者一律拒绝。
FIELD_SCOPE = {
    # —— 支付正确性域（医保局）——
    "group_code":            {PAYER, HEALTH, HOSPITAL},   # 分组结果，双方都看
    "group_weight":          {PAYER, HEALTH, HOSPITAL},
    "payment_amount":        {PAYER, HOSPITAL},
    "upcoding_flag":         {PAYER},                     # 高套标记
    "upcoding_loss_amount":  {PAYER},                     # 法定损失金额
    "undercoding_gap":       {HOSPITAL},                  # 低编回补，只给医院
    "denial_flag":           {PAYER, HOSPITAL},           # 拒付标记——不给卫健委
    "denial_amount":         {PAYER, HOSPITAL},
    "violation_score":       {PAYER},                     # 违规评分——不给卫健委
    "referral_status":       {PAYER},                     # 移送状态
    # —— 医疗质量域（卫健委）——
    "cmi":                   {HEALTH, HOSPITAL},
    "level4_surgery_rate":   {HEALTH, HOSPITAL},
    "low_risk_mortality":    {HEALTH, HOSPITAL},
    "time_consumption_idx":  {HEALTH, HOSPITAL},
    "cost_consumption_idx":  {HEALTH, HOSPITAL},
    "severe_case_rate":      {HEALTH, HOSPITAL},
    "readmission_30d":       {HEALTH, HOSPITAL},
    # —— 共用的结构性指标 ——
    "case_count":            {PAYER, HEALTH, HOSPITAL},
    "avg_los":               {PAYER, HEALTH, HOSPITAL},
}

# 明确禁止的跨域流向。违反即拒绝，不降级为告警。
FORBIDDEN_FLOWS = [
    (PAYER, HEALTH, {"denial_flag", "denial_amount", "violation_score",
                     "upcoding_flag", "upcoding_loss_amount", "referral_status"},
     "医保局的拒付与违规信号流入卫健委绩效考核，会叠加成「拒付→记分→绩效」链条，"
     "是促使医院污染数据的根因"),
    (HEALTH, PAYER, {"cmi", "level4_surgery_rate", "low_risk_mortality",
                     "time_consumption_idx", "cost_consumption_idx"},
     "卫健委的质量评分回流医保局用作拒付依据——质量差不是违规，"
     "不构成不予支付的法定理由"),
]


class ScopeDenied(Exception):
    """越权访问或禁止的跨域流向——拒绝执行。"""


def authorize(subject: str, fields) -> dict:
    """按主体裁剪可访问字段。越权字段逐一列出，不静默丢弃。"""
    if subject not in (PAYER, HEALTH, HOSPITAL):
        raise ScopeDenied(f"未注册主体「{subject}」")
    allowed, denied = [], []
    for f in fields:
        scope = FIELD_SCOPE.get(f)
        if scope is None:
            denied.append((f, "字段未注册"))
        elif subject in scope:
            allowed.append(f)
        else:
            denied.append((f, f"该字段仅限 {sorted(scope)}"))
    if denied:
        raise ScopeDenied(
            f"主体「{subject}」请求越权字段：" +
            "；".join(f"{f}（{why}）" for f, why in denied))
    return dict(主体=subject, 可访问字段=allowed)


def assert_flow(src: str, dst: str, fields) -> None:
    """跨主体数据流向检查。"""
    for a, b, banned, why in FORBIDDEN_FLOWS:
        if src == a and dst == b:
            hit = sorted(set(fields) & banned)
            if hit:
                raise ScopeDenied(
                    f"禁止的跨域流向 {a} → {b}：{hit}。{why}。")


def view(subject: str, record: dict) -> dict:
    """返回该主体视角下的 DRG 记录。"""
    keep = {k: v for k, v in record.items()
            if subject in FIELD_SCOPE.get(k, set())}
    return dict(主体=subject, 记录=keep,
                已屏蔽=sorted(set(record) - set(keep)) or ["无"])


def interface_matrix():
    """接口矩阵，供对外说明。"""
    rows = []
    for f, scope in FIELD_SCOPE.items():
        rows.append(dict(字段=f,
                         医保局="✓" if PAYER in scope else "—",
                         卫健委="✓" if HEALTH in scope else "—",
                         医院="✓" if HOSPITAL in scope else "—"))
    return rows


def audit_separation():
    """不变量：两域的专属字段不得有交集。"""
    payer_only = {f for f, s in FIELD_SCOPE.items() if s == {PAYER}}
    health_only = {f for f, s in FIELD_SCOPE.items() if s == {HEALTH, HOSPITAL}}
    assert not (payer_only & health_only), "两域专属字段出现交集"
    return dict(医保局专属=sorted(payer_only), 卫健委域=sorted(health_only),
                交集=[], 禁止流向数=len(FORBIDDEN_FLOWS),
                结论="两域分区完整；拒付类信号不出现在卫健委视图中")
