"""药学诊查类项目工作记录规范化。

工作记录在这里有双重身份：**既是专业文书，也是收费的发生依据**。
后一重身份决定了它的约束比一般病历更严：

    记录必须在**服务发生时**产生，而不是在结算时补齐。
    结算时补录的记录，无法证明服务发生过——它只能证明有人想收这笔费。

与长护险服务真实性核验同构：真实性锚定在「记录与服务的时间关系」上，
而不是锚定在记录写得好不好。

⚠ 红线：记录的作用是**证明服务发生**，不是**证明服务有效**。
不得用记录完整度去评判药师的专业判断——两者是不同的东西，
混为一谈会逼出「为通过检查而写的记录」，重演病案首页以「能过审」为目标的老问题。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

# 最小要素集：缺一即不足以证明服务发生
MIN_ELEMENTS = {
    "药学门诊":   {"服务对象", "服务时间", "用药史采集", "评估结论",
                   "干预建议", "执行药师"},
    "用药监测与评估": {"服务对象", "服务时间", "监测指标", "评估结论",
                       "执行药师"},
    "药学查房":   {"服务对象", "服务时间", "查房发现", "干预建议", "执行药师"},
    "用药教育":   {"服务对象", "服务时间", "教育内容", "理解确认", "执行药师"},
}

# 补录判定阈值
BACKFILL_WINDOW_H = 24      # 服务后多久之内记录视为及时
BURST_THRESHOLD = 5         # 同一药师同一分钟内生成多少条视为批量补录


class RecordRejected(Exception):
    pass


@dataclass
class WorkRecord:
    record_id: str
    item_name: str
    patient_ref: str            # 去标识化引用，不是患者身份
    pharmacist: str
    service_time: str           # 服务实际发生时间
    record_time: str            # 记录写入时间
    settle_time: str | None     # 结算时间
    elements: dict = field(default_factory=dict)

    def _dt(self, s):
        return datetime.fromisoformat(s) if s else None


def check_completeness(r: WorkRecord) -> dict:
    need = MIN_ELEMENTS.get(r.item_name)
    if need is None:
        raise RecordRejected(f"未知项目「{r.item_name}」，可选：{sorted(MIN_ELEMENTS)}")
    present = {k for k, v in r.elements.items() if str(v).strip()}
    missing = sorted(need - present)
    return dict(record_id=r.record_id, 项目=r.item_name,
                齐备=not missing, 缺失要素=missing,
                判定="要素齐备" if not missing else "要素不足以证明服务发生")


def check_timeliness(r: WorkRecord) -> dict:
    """时间顺序约束：服务 → 记录 → 结算。顺序错了，记录就不是发生依据。"""
    st, rt = r._dt(r.service_time), r._dt(r.record_time)
    bt = r._dt(r.settle_time)
    issues = []
    if rt < st:
        issues.append("记录时间早于服务时间——记录了尚未发生的服务")
    lag_h = (rt - st).total_seconds() / 3600
    if lag_h > BACKFILL_WINDOW_H:
        issues.append(f"记录滞后 {lag_h:.1f} 小时，超过 {BACKFILL_WINDOW_H} 小时窗口")
    if bt and rt > bt:
        issues.append("记录时间晚于结算时间——先收费后补记录，"
                      "该记录不能作为服务发生的证据")
    return dict(record_id=r.record_id, 记录滞后小时=round(lag_h, 2),
                问题=issues or ["无"],
                判定="时间链成立" if not issues else "时间链不成立")


def check_backfill(records) -> dict:
    """批量补录检出：同一药师在同一分钟内生成大量记录。

    一个药师不可能在一分钟内完成多次药学诊查。这是**机械信号**，
    不依赖任何真值标注，因此可在冷启动阶段直接使用。
    """
    buckets = {}
    for r in records:
        key = (r.pharmacist, r.record_time[:16])     # 精确到分钟
        buckets.setdefault(key, []).append(r.record_id)
    hits = [dict(药师=k[0], 时刻=k[1], 条数=len(v), 记录=v[:8])
            for k, v in buckets.items() if len(v) >= BURST_THRESHOLD]
    return dict(批量补录疑似=len(hits), 明细=sorted(hits, key=lambda x: -x["条数"]),
                阈值=f"同一药师同一分钟 ≥ {BURST_THRESHOLD} 条",
                说明="机械信号，不依赖真值标注；但须人工核实后定性——"
                     "也可能是记录系统批量同步造成的时间戳塌缩")


def authenticity_signal(r: WorkRecord) -> dict:
    """汇总真实性信号。只回答「服务是否发生」，不回答「服务是否有效」。"""
    c, t = check_completeness(r), check_timeliness(r)
    ok = c["齐备"] and t["判定"] == "时间链成立"
    return dict(record_id=r.record_id,
                真实性="可采信" if ok else "待核",
                要素=c["判定"], 时间链=t["判定"],
                问题=([] if c["齐备"] else [f"缺要素 {c['缺失要素']}"]) +
                     [i for i in t["问题"] if i != "无"],
                边界="本判定只涉及服务是否发生；服务质量与药师专业判断"
                     "不在本模块评价范围内")


def assert_not_quality_score(purpose: str) -> None:
    """红线：不得用记录完整度评判药师专业水平。"""
    BANNED = ("质量评分", "绩效", "考核药师", "排名", "professional rating")
    hit = [b for b in BANNED if b in purpose]
    if hit:
        raise RecordRejected(
            f"用途声明含「{hit}」——记录规范性不得用作药师专业水平评价。"
            f"记录证明服务发生，不证明服务有效。"
            f"混为一谈会逼出「为通过检查而写的记录」。")
