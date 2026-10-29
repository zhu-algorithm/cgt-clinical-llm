"""风险护盾（Risk Shield）—— 开医嘱时拦截，而非结算后追回。

对医生的价值不是「少扣钱」，是「不挨处分」：
2025 年全国首次披露医保支付资格记分 16.36 万人次（国家医保局，2026-07-23），
实现「监管到人」。对医生个人，一次记分的代价远超任何单笔拒付。

政策依据：《实施细则》要求健全事前、事中、事后相结合的全流程智能监管体系；
国家医保局要求 2026 年底前定点医药机构事前提醒系统接入率达 70% 以上
（截至 2026-06-30 全国实际已达 71%）。

⚠ 两条硬约束：

  **超时必须放行，不得阻断诊疗。** 一个响应慢的提醒系统若默认阻断，
  代价由患者承担。SLA 超时即 fail-open，并记录超时事件供治理层审计。

  **提醒金额不是避免损失。** 全国 2025 年事前提醒 181.26 亿元，
  而事中拒付仅 22.79 亿元（约 12.6%）。本模块输出提醒笔数与类型，
  **不输出「避免损失金额」**——后者须由对照组校正后方可计量。
"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, wait

LEVELS = ("通过", "提示", "需确认", "建议改方")
DEFAULT_SLA_MS = 800


class ShieldResult(dict):
    pass


def pre_order_check(order, checkers, sla_ms: int = DEFAULT_SLA_MS) -> ShieldResult:
    """开医嘱时检查。checkers 为 (名称, 函数) 列表，函数返回 None 或 dict。

    截止时间施加在**检查本身**上，而不是两次检查之间：
    单个慢检查必须被弃用，而不是等它跑完再宣布超时——
    否则 fail-open 形同虚设，患者仍要为系统响应慢买单。

    被弃用的线程不强杀（Python 无法安全中断），但不再等待其结果。
    """
    t0 = time.perf_counter()
    hits, timed_out, abandoned = [], False, []
    ex = ThreadPoolExecutor(max_workers=max(len(checkers), 1))
    try:
        futs = {ex.submit(fn, order): name for name, fn in checkers}
        # 整体截止：所有检查并行跑，到点就收——快的检查不被慢的连累
        done, not_done = wait(futs, timeout=sla_ms / 1000.0)
        for fut in done:
            try:
                r = fut.result()
            except Exception as e:      # 检查器自身异常不得阻断诊疗
                timed_out = True
                abandoned.append(f"{futs[fut]}（异常 {type(e).__name__}）")
                continue
            if r:
                hits.append(dict(检查=futs[fut], **r))
        for fut in not_done:
            timed_out = True
            abandoned.append(futs[fut])
            fut.cancel()
    finally:
        # 不能 wait=True：那会把被弃用的慢检查又等完一遍，fail-open 形同虚设
        ex.shutdown(wait=False)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    if timed_out:
        return ShieldResult(级别="通过", 原因="SLA 超时-放行", 耗时ms=elapsed,
                            命中=hits, 超时=True, 弃用检查=abandoned,
                            说明="超时 fail-open：不得因系统响应慢而阻断诊疗；"
                                 "该事件计入系统超时率，由治理层考核。"
                                 "已完成的检查结果仍然返回，但不作为阻断依据")
    if not hits:
        return ShieldResult(级别="通过", 耗时ms=elapsed, 命中=[], 超时=False)
    sev = max(h.get("严重度", 1) for h in hits)
    lvl = LEVELS[min(sev, 3)]
    return ShieldResult(级别=lvl, 耗时ms=elapsed, 命中=hits, 超时=False,
                        医生侧收益=("避免支付资格记分与事后追回，而非「少扣钱」"
                                    if sev >= 2 else "合规提示"))


def doctor_exposure(order_history):
    """医生侧风险敞口：按已命中但未改方的记录估计记分暴露面。

    输出的是**暴露笔数与类型**，不输出金额——提醒金额不等于避免损失。
    """
    unresolved = [o for o in order_history
                  if o.get("级别") in ("需确认", "建议改方") and not o.get("已改方")]
    by_type = {}
    for o in unresolved:
        for h in o.get("命中", []):
            by_type[h["检查"]] = by_type.get(h["检查"], 0) + 1
    return dict(未处理高风险医嘱=len(unresolved), 分类型=by_type,
                说明="本模块不输出「已避免损失金额」；"
                     "提醒金额是触发提示的金额，真实避免须用对照组校正")


def adoption_metrics(order_history):
    """采纳率与超时率——这两项考核的是系统，不是医生。"""
    n = len(order_history) or 1
    flagged = [o for o in order_history if o.get("级别") != "通过"]
    adopted = [o for o in flagged if o.get("已改方")]
    to = [o for o in order_history if o.get("超时")]
    return dict(医嘱总数=len(order_history), 命中数=len(flagged),
                采纳改方数=len(adopted),
                采纳率=round(len(adopted)/len(flagged), 4) if flagged else None,
                超时率=round(len(to)/n, 4),
                说明="采纳率低提示提醒质量差或打扰过度，须回查误报率，"
                     "不得以「医生不配合」结案")
