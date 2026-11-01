"""药学人工智能体评价机制。

评价一个药学智能体，最容易犯的错是**用它拦截了多少钱来评价它**。
那是供方侧指标，且与红线冲突：按拦截金额优化，会把模型训成拒付偏向。

本模块规定的评价口径：
  · 基准是**药师共识**，不是规则命中——规则命中只能证明实现正确，
    不能证明判断正确
  · 必须测**漏检率**，不只测检出率——只报检出率的系统，
    把全部否定的模型也能报出高准确率
  · 必须**双盲**——复核药师不得看到智能体的判断
  · 一致率必须用 kappa，不能用原始一致率（后者在类别极不平衡时退化）
"""
from __future__ import annotations
import math

try:                                    # 复用既有统计实现，避免两套口径
    from fmed_rx.evaluation import wilson, cohen_kappa
except Exception:                       # 独立运行时的回退实现
    def wilson(k, n, z=1.96):
        if n == 0: return (0.0, 1.0)
        p = k / n; d = 1 + z*z/n
        c = (p + z*z/(2*n)) / d
        h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
        return (max(0.0, c-h), min(1.0, c+h))

    def cohen_kappa(a11, a10, a01, a00):
        n = a11 + a10 + a01 + a00
        if n == 0: return 0.0
        po = (a11 + a00) / n
        pe = ((a11+a10)*(a11+a01) + (a01+a00)*(a10+a00)) / (n*n)
        return 0.0 if pe == 1 else (po - pe) / (1 - pe)


EVAL_DIMENSIONS = {
    "检出率":   "智能体识别出的真实问题 ÷ 全部真实问题（需金标准）",
    "漏检率":   "1 − 检出率。**必须单独报告**——只报检出率会掩盖全盘漏检",
    "误报率":   "智能体标记但药师判定无问题 ÷ 智能体全部标记",
    "kappa":    "与药师共识的一致性，扣除偶然一致。类别不平衡时必须用它",
    "采纳率":   "医师实际采纳的建议 ÷ 智能体全部建议——考核系统，不考核医师",
    "超时率":   "超出 SLA 的比例——考核系统",
}

FORBIDDEN_METRICS = {
    "拦截金额": "供方侧指标；按此优化会把模型训成拒付偏向",
    "节省金额": "提醒金额不等于避免损失，须用对照组校正",
    "拦截笔数": "同上，且鼓励以量取胜而非准确",
    "拒付率":   "供方侧指标，不得作为药学智能体的评价口径",
}


class MetricRefused(Exception):
    pass


def assert_metric_allowed(name: str) -> None:
    for bad, why in FORBIDDEN_METRICS.items():
        if bad in name:
            raise MetricRefused(f"指标「{name}」不受理：{why}。"
                                f"可用口径：{sorted(EVAL_DIMENSIONS)}")


def blinded_review_design(n_cases: int, n_reviewers: int = 2) -> dict:
    """双盲复核设计。"""
    return dict(
        病例数=n_cases, 复核药师数=n_reviewers,
        设计=["复核药师**不得看到**智能体的判断与置信度",
              "病例顺序随机，智能体标记阳性与阴性的病例按比例混合呈现",
              "阴性样本必须足量纳入——只复核阳性样本无法估计漏检率",
              "两名药师独立判定，分歧由第三人仲裁，仲裁结果为金标准"],
        常见错误="只把智能体标记的阳性病例交给药师复核。"
                 "这样算出的只是阳性预测值，**漏检率完全不可知**，"
                 "而漏检才是基金损失的主要来源")


def concordance(agent_calls, pharmacist_calls) -> dict:
    """智能体与药师共识的一致性。"""
    n = len(agent_calls)
    if n != len(pharmacist_calls) or n == 0:
        raise MetricRefused("两组判定长度不一致或为空")
    raw = sum(1 for a, p in zip(agent_calls, pharmacist_calls) if a == p) / n
    tp = sum(1 for a, p in zip(agent_calls, pharmacist_calls) if a and p)
    fp = sum(1 for a, p in zip(agent_calls, pharmacist_calls) if a and not p)
    fn = sum(1 for a, p in zip(agent_calls, pharmacist_calls) if not a and p)
    tn = n - tp - fp - fn
    # 复用 fmed_rx.evaluation 的实现，避免两套 kappa 口径
    k = cohen_kappa(tp, fp, fn, tn)
    detect = tp / (tp + fn) if (tp + fn) else None
    lo, hi = wilson(tp, tp + fn) if (tp + fn) else (None, None)
    return dict(样本数=n, 原始一致率=round(raw, 4), kappa=round(k, 4),
                检出率=round(detect, 4) if detect is not None else None,
                漏检率=round(1 - detect, 4) if detect is not None else None,
                检出率95CI下界=round(lo, 4) if lo is not None else None,
                误报率=round(fp / (tp + fp), 4) if (tp + fp) else None,
                提示="原始一致率在阳性稀少时会虚高——以 kappa 为准")


def deployment_gate(agent_calls, pharmacist_calls,
                    kappa_min: float = 0.60, detect_lb_min: float = 0.60) -> dict:
    """上线闸门：kappa 与检出率置信下界**双闸门**。

    用置信下界而非点估计：n=100、检出 60 例时点估计 0.60 看似达标，
    但 Wilson 下界只有 0.502——样本量不足以支持该结论。
    """
    c = concordance(agent_calls, pharmacist_calls)
    reasons = []
    if c["kappa"] < kappa_min:
        reasons.append(f"kappa {c['kappa']} < {kappa_min}")
    if c["检出率95CI下界"] is None or c["检出率95CI下界"] < detect_lb_min:
        reasons.append(f"检出率95%置信下界 {c['检出率95CI下界']} < {detect_lb_min}"
                       f"（点估计 {c['检出率']} 可能达标，但样本量不足以支持）")
    return dict(**c, 闸门="通过" if not reasons else "不通过",
                不通过原因=reasons or ["无"],
                说明="两道闸门必须同时通过；任一不过即不得上线")
