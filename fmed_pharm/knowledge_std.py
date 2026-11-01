"""药学人工智能知识库构建标准。

知识库不是文档堆。要让智能体「每条判定都有理有据」，知识库必须回答三个问题：
  1. 这条知识来自哪里，效力等级是什么
  2. 来源之间冲突时怎么办
  3. 按费用发生当天的版本，这条知识当时是什么样

最容易犯的错是第 2 条：把冲突消解简化成「取效力最高的来源」。
那会直接导致**把超说明书用药一律判为违规**——而这在法律上是错的。

法律依据（已核原文）
--------------------
《中华人民共和国医师法》（2021-08-20 十三届全国人大常委会第三十次会议通过，
2022-03-01 施行）第二十九条：

  第一款　医师应当坚持安全有效、经济合理的用药原则，遵循药品临床应用指导原则、
          临床诊疗指南和药品说明书等合理用药。
  第二款　在尚无有效或者更好治疗手段等特殊情况下，医师取得患者明确知情同意后，
          可以采用药品说明书中未明确但具有循证医学证据的药品用法实施治疗。
          医疗机构应当建立管理制度，对医师处方、用药医嘱的适宜性进行审核，
          严格规范医师用药行为。

即：按说明书用药是基础，超说明书用药是**法定例外**，不是违规。
第二款设定四个条件，缺一不可。知识库必须能表达「例外成立」这件事，
否则智能体会把合法行为判成违规——这是比漏检更严重的错误类型。

⚠ 医保支付与临床合法性是两件事：超说明书用药合法，**不等于**医保基金应当支付。
本模块分别输出「临床合法性」与「支付立场」，不把两者混成一个结论。
"""
from __future__ import annotations
from dataclasses import dataclass, field

# ── 来源效力分级 ──────────────────────────────────────────
# level 越小效力越高。但效力高**不等于**可以直接推翻低效力来源，见 resolve()
SOURCE_HIERARCHY = {
    "药品说明书":       dict(level=1, 性质="法定文件", 可否单独定违规=True),
    "国家医保药品目录": dict(level=1, 性质="支付范围法定依据", 可否单独定违规=True),
    "国家监管两库":     dict(level=1, 性质="监管规则", 可否单独定违规=True),
    "药品临床应用指导原则": dict(level=2, 性质="行政规范", 可否单独定违规=False),
    "临床诊疗指南":     dict(level=2, 性质="专业规范", 可否单独定违规=False),
    "专家共识":         dict(level=3, 性质="专业意见", 可否单独定违规=False),
    "超说明书用药目录": dict(level=3, 性质="循证汇编", 可否单独定违规=False),
    "同行评议文献":     dict(level=4, 性质="证据", 可否单独定违规=False),
}

# 可商用许可白名单——与接入通道保持同一口径
OK_LICENCES = {"CC0", "CC BY", "CC-BY", "PUBLIC DOMAIN", "公有领域", "法定公开"}


class KnowledgeRejected(Exception):
    pass


@dataclass
class Entry:
    """一条知识库条目。缺任一必填项即不得入库。"""
    entry_id: str
    source_type: str
    citation: str                  # 发文机关＋文号＋条款号，或 期刊＋DOI
    statement: str
    effective_from: str            # as-of：本条生效日
    effective_to: str | None = None
    licence: str = "法定公开"
    evidence_grade: str | None = None   # 1A/1B/2A/2B/3 等
    url: str = ""

    def active_on(self, date: str) -> bool:
        if date < self.effective_from:
            return False
        return self.effective_to is None or date <= self.effective_to


def admit(e: Entry) -> Entry:
    """入库校验。"""
    if e.source_type not in SOURCE_HIERARCHY:
        raise KnowledgeRejected(
            f"未知来源类型「{e.source_type}」，可选：{sorted(SOURCE_HIERARCHY)}")
    if not e.citation.strip():
        raise KnowledgeRejected(
            f"条目 {e.entry_id} 缺引用：每条判定都必须可追溯到具体出处——"
            f"法规类须给出发文机关＋文号＋条款号")
    for bad in ("模型生成", "综合整理", "据了解", "业内通行", "根据经验"):
        if bad in e.citation:
            raise KnowledgeRejected(
                f"条目 {e.entry_id} 引用标注为「{bad}」，不得作为判定依据")
    lic = e.licence.strip().upper()
    if not any(ok in lic for ok in {x.upper() for x in OK_LICENCES}):
        raise KnowledgeRejected(
            f"条目 {e.entry_id} 许可「{e.licence}」不在可商用白名单内：{sorted(OK_LICENCES)}")
    if not e.effective_from:
        raise KnowledgeRejected(f"条目 {e.entry_id} 缺生效日期——无法做 as-of 裁定")
    return e


def as_of(entries, date: str):
    """按费用发生当天的版本取知识，拒绝回退到当前版本。

    用今天的规则去判昨天的费用，是追溯性执法。
    """
    hit = [e for e in entries if e.active_on(date)]
    if not hit:
        raise KnowledgeRejected(
            f"{date} 当日无生效条目，且**拒绝回退到当前版本**——"
            f"用今天的规则判昨天的费用构成追溯性执法")
    return hit


# ── 冲突消解 ──────────────────────────────────────────────
def resolve(entries, date: str) -> dict:
    """来源冲突消解。输出**立场**，不输出判定。

    关键：效力高的来源不能自动推翻效力低的来源。
    说明书未载明、而指南有推荐，这不是矛盾——这正是《医师法》第二十九条
    第二款预设的情形，须走 off_label_pathway() 判断例外是否成立。
    """
    act = as_of(entries, date)
    by_level = {}
    for e in act:
        by_level.setdefault(SOURCE_HIERARCHY[e.source_type]["level"], []).append(e)
    top = by_level[min(by_level)]
    conflict = len({e.statement for e in act}) > 1
    label_silent = not any(e.source_type == "药品说明书" for e in act)
    guideline_backed = any(SOURCE_HIERARCHY[e.source_type]["level"] in (2, 3)
                           for e in act)
    if label_silent and guideline_backed:
        return dict(立场="说明书未载明而存在循证支持",
                    处置="转 off_label_pathway() 判断法定例外是否成立",
                    依据="《医师法》第二十九条第二款",
                    可否据此定违规=False,
                    条目数=len(act), 冲突=conflict,
                    说明="说明书沉默不等于禁止；把这种情形判为违规，"
                         "会把法定例外当成违法")
    return dict(立场="最高效力来源的表述",
                最高效力来源=[e.source_type for e in top],
                表述=[e.statement for e in top],
                可否据此定违规=any(
                    SOURCE_HIERARCHY[e.source_type]["可否单独定违规"] for e in top),
                条目数=len(act), 冲突=conflict,
                引用=[e.citation for e in top])


# ── 超说明书用药的法定例外 ────────────────────────────────
OFF_LABEL_CONDITIONS = {
    "无更好治疗手段": "尚无有效或者更好治疗手段等特殊情况",
    "患者知情同意":   "医师取得患者明确知情同意",
    "循证医学证据":   "具有循证医学证据的药品用法",
    "机构审核制度":   "医疗机构建立管理制度并对处方、用药医嘱适宜性审核",
}


def off_label_pathway(conditions: dict) -> dict:
    """《医师法》第二十九条第二款四条件判定。四者缺一不可。

    输出分两层，**不得合并**：
      · 临床合法性——例外是否成立
      · 支付立场——即便合法，医保是否应当支付是另一个问题
    """
    met = {k: bool(conditions.get(k)) for k in OFF_LABEL_CONDITIONS}
    missing = [k for k, v in met.items() if not v]
    legal = not missing
    return dict(
        法律依据="《中华人民共和国医师法》第二十九条第二款（2022-03-01 施行）",
        四条件=met, 未满足=missing or ["无"],
        临床合法性="法定例外成立" if legal else "例外不成立",
        支付立场=("须另行判断：合法不等于基金应当支付，"
                  "支付范围以医保目录与限定支付范围为准"
                  if legal else "例外不成立，且不在说明书范围内"),
        边界="本函数不输出拒付决定。临床合法性与支付责任是两件事，"
             "把两者合并会导致：要么为不该付的买单，"
             "要么把合法诊疗判成骗保")


def assert_not_blanket_denial(policy: str) -> None:
    """红线：不得把「超说明书用药」整体设为违规规则。"""
    BAD = ("超说明书一律", "超说明书全部拒付", "凡超说明书", "超适应症一律")
    hit = [b for b in BAD if b in policy]
    if hit:
        raise KnowledgeRejected(
            f"策略含「{hit}」——超说明书用药是《医师法》第二十九条第二款"
            f"规定的法定例外，不得整体设为违规规则。"
            f"应逐例走四条件判定；把合法诊疗判成违规，"
            f"比漏检更严重：它会直接损害患者可及性。")
