"""门诊慢特病临床用药三类违规检出。

架构要点 —— 三类检查的性质不同，不能用同一套方法做：

  超量用药  确定性。成分归一 + 窗口内剂量累加 + 说明书上限比较。
            规则引擎完成，大模型不参与。用大模型做会引入幻觉且不可审计。
  重复用药  确定性。同成分重复 / 同药理类别重复。规则引擎完成。
  超适应症  **唯一需要大模型的一类**。说明书适应症是自然语言，
            病历诊断叙述也是自然语言，二者的蕴含关系无法用规则表达。
            这是 F-Med 在本模块中不可替代的位置，也是唯一允许其介入之处。
            输出必须附条文级溯源，且不得单独触发拒付。
"""
from __future__ import annotations
from collections import defaultdict

from .ingredients import decompose
from .dose_limits import MAX_DAILY_MG, THERAPEUTIC_CLASS


def _daily_mg(total_mg: float, days: float) -> float:
    return total_mg / days if days and days > 0 else float("nan")


def check_overdose(prescriptions, window_days: float):
    """超量用药。prescriptions: [{drug, n_units, strength_mg, days}]

    按成分归集后再比上限——这是关键。同一成分分散在多张处方、
    或藏在复方制剂里，逐方检查都查不出来。
    """
    agg = defaultdict(float)
    prov = defaultdict(list)
    for p in prescriptions:
        for ing, mg in decompose(p["drug"], p["n_units"], p.get("strength_mg")):
            if mg != mg:          # NaN：剂量缺失，不可判定
                prov[ing].append((p["drug"], "剂量缺失-不可判定"))
                continue
            agg[ing] += mg
            prov[ing].append((p["drug"], f"{mg:.2f}mg"))

    out = []
    for ing, total in agg.items():
        lim = MAX_DAILY_MG.get(ing)
        if not lim:
            out.append(dict(成分=ing, 判定="无上限数据-不可判定",
                            日均mg=round(_daily_mg(total, window_days), 3),
                            来源=prov[ing]))
            continue
        d = _daily_mg(total, window_days)
        viol = d > lim["max_mg"] * 1.0001        # 容差防浮点误判
        out.append(dict(成分=ing, 判定="超量用药" if viol else "合规",
                        日均mg=round(d, 3), 上限mg=lim["max_mg"],
                        超出倍数=round(d / lim["max_mg"], 3),
                        依据=lim["src"], 来源=prov[ing]))
    return out


def check_duplicate(prescriptions):
    """重复用药：同成分重复开具，或同药理类别内多成分并用。"""
    ings = defaultdict(list)
    for p in prescriptions:
        for ing, _ in decompose(p["drug"], p["n_units"], p.get("strength_mg")):
            ings[ing].append(p["drug"])

    findings = []
    for ing, srcs in ings.items():
        if len(srcs) > 1:
            findings.append(dict(类型="同成分重复", 成分=ing, 涉及=srcs))

    by_class = defaultdict(set)
    for ing in ings:
        c = THERAPEUTIC_CLASS.get(ing)
        if c:
            by_class[c].add(ing)
    for c, members in by_class.items():
        if len(members) > 1:
            findings.append(dict(类型="同类重复", 药理类别=c, 成分=sorted(members)))
    return findings


def check_off_label(prescriptions, diagnoses, llm_judge=None):
    """超适应症用药 —— 需要 F-Med 判定头。

    llm_judge(drug, indications_text, diagnoses) -> {'entailed': bool,
                                                    'citation': str,
                                                    'confidence': float}
    未注入判定器时返回"待判定"，**绝不默认合规也绝不默认违规**——
    把不可判定当成合规会漏检，当成违规会误拒，两者都不可接受。
    """
    out = []
    for p in prescriptions:
        if llm_judge is None:
            out.append(dict(药品=p["drug"], 判定="待判定",
                            说明="需 F-Med 适应症判定头 + 说明书条文检索"))
        else:
            r = llm_judge(p["drug"], p.get("indications", ""), diagnoses)
            out.append(dict(药品=p["drug"],
                            判定="超适应症" if not r["entailed"] else "合规",
                            溯源=r.get("citation"), 置信度=r.get("confidence")))
    return out


def triage(overdose, duplicate, off_label):
    """分级管控。见 FMED_INSURANCE_AGENT_CN.md 第二节。

    L3（暂停结算资格）不在本函数产出范围——该动作须人工审定。
    """
    hard = [o for o in overdose if o["判定"] == "超量用药"]
    undecidable = [o for o in overdose if "不可判定" in o["判定"]]
    if hard:
        lvl, act = "L2-事前审核", "结算前人工复核；可回溯至说明书上限条文"
    elif duplicate:
        lvl, act = "L1-柔性提示", "事中提示，不阻断"
    elif undecidable:
        lvl, act = "L0-数据质量", "剂量或上限缺失，转数据治理，不得据此拒付"
    else:
        lvl, act = "L0-无异常", "放行"
    return dict(级别=lvl, 处置=act,
                硬命中=len(hard), 重复=len(duplicate), 不可判定=len(undecidable),
                待判定超适应症=sum(1 for x in off_label if x["判定"] == "待判定"))
