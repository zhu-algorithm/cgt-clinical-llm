"""DRG 运营分析 —— 医院管理层最关心的经营分析。

病组盈亏、成本结构拆解、临床路径偏差提示、特例单议申报辅助
（DRG 5%、DIP 5‰ 额度内的合理申报）。

⚠ 一条边界：本模块服务医院经营优化，**不得用于指导病例选择**。
  「病组盈亏」若被用来推诿亏损病组患者，就是推诿重症。
  因此模块同时输出 CMI 与重症收治率，并在亏损病组分析中
  强制附带「该病组收治量变化」监测——收治量下降须解释。
"""
from __future__ import annotations
import statistics


def group_margin(cases):
    """病组盈亏。cases: [{drg, payment, cost, is_severe}]"""
    by = {}
    for c in cases:
        by.setdefault(c["drg"], []).append(c)
    rows = []
    for drg, cs in by.items():
        pay = [c["payment"] for c in cs]; cost = [c["cost"] for c in cs]
        margin = sum(pay) - sum(cost)
        rows.append(dict(DRG=drg, 例数=len(cs),
                         次均支付=round(statistics.mean(pay), 1),
                         次均成本=round(statistics.mean(cost), 1),
                         盈亏=round(margin, 1),
                         次均盈亏=round(margin/len(cs), 1),
                         重症占比=round(sum(c.get("is_severe", False) for c in cs)/len(cs), 3),
                         判定="亏损病组" if margin < 0 else "盈余病组"))
    return sorted(rows, key=lambda r: r["次均盈亏"])


def dumping_watch(rows_t0, rows_t1, drop_threshold=0.20):
    """推诿监测：亏损病组的收治量是否异常下降。

    这是本模块的安全闸门——盈亏分析一旦被用于病例选择即为推诿重症。
    """
    a = {r["DRG"]: r for r in rows_t0}
    out = []
    for r in rows_t1:
        prev = a.get(r["DRG"])
        if not prev or prev["例数"] < 10:
            continue
        chg = (r["例数"] - prev["例数"]) / prev["例数"]
        if prev["判定"] == "亏损病组" and chg <= -drop_threshold:
            out.append(dict(DRG=r["DRG"], 前期例数=prev["例数"], 本期例数=r["例数"],
                            变化=f"{chg:+.1%}", 前期判定=prev["判定"],
                            判定="**疑似推诿亏损病组**",
                            处置="须由医院说明收治量下降原因；"
                                 "无合理解释则触发质量守门指标告警"))
    return out


def special_case_quota(total_cases, applied, payment_type="DRG"):
    """特例单议额度：DRG 5%、DIP 5‰。"""
    cap_rate = 0.05 if payment_type == "DRG" else 0.005
    cap = int(total_cases * cap_rate)
    return dict(支付方式=payment_type, 总例数=total_cases,
                额度比例=f"{cap_rate:.1%}" if payment_type == "DRG" else "5‰",
                可申报上限=cap, 已申报=applied, 剩余额度=max(cap - applied, 0),
                判定="超额度" if applied > cap else "额度内",
                说明="额度内的合理申报是政策允许的救济通道，不是违规")


def pathway_deviation(cases, pathway):
    """临床路径偏差：实际项目与路径标准项目的差集。"""
    std = set(pathway)
    out = []
    for c in cases:
        act = set(c["items"])
        out.append(dict(case_id=c["case_id"],
                        缺失项=sorted(std - act), 额外项=sorted(act - std),
                        判定=("路径内" if act == std else "存在偏差"),
                        说明="偏差不等于违规——个体化治疗本就会偏离标准路径；"
                             "本项仅供临床路径管理参考"))
    return out


def quality_counterweight(cases):
    """与盈亏分析同权输出的质量指标，防止经营优化压过医疗质量。"""
    n = len(cases) or 1
    severe = sum(c.get("is_severe", False) for c in cases)
    cmi = statistics.mean([c.get("weight", 1.0) for c in cases])
    return dict(CMI=round(cmi, 3), 重症收治率=round(severe/n, 4), 例数=len(cases),
                说明="CMI 与重症收治率须与盈亏分析同权呈现；"
                     "两者下降而盈亏改善，意味着利润来自病例选择而非效率提升")
