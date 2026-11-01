import sys, json; sys.path.insert(0, ".")
from fmed_pharm.price_item import (ServiceRecord, report, assert_not_suppression,
                                   EvaluationMisuse, PRICE_ITEM_CLASSES)
from fmed_pharm.pharm_record import (WorkRecord, authenticity_signal, check_backfill,
                                     assert_not_quality_score, RecordRejected)
from fmed_pharm.agent_eval import (deployment_gate, blinded_review_design,
                                   assert_metric_allowed, MetricRefused)
from fmed_pharm.knowledge_std import (Entry, admit, resolve, off_label_pathway,
                                      as_of, assert_not_blanket_denial, KnowledgeRejected)

print("=== 1 价格项目运行评估（双向）===")
INST = [f"H{i}" for i in range(1, 11)]
recs = []
for i in range(1, 6):   # 5 家正常开展并收费
    recs.append(ServiceRecord(f"R{i}", "药学诊查", "药学门诊", f"H{i}", f"P{i}",
                              "2026-09-10T09:00", True, True, 50.0, "黄浦"))
for i in range(6, 9):   # 3 家做了没收费 → 漏收
    recs.append(ServiceRecord(f"R{i}", "药学诊查", "药学门诊", f"H{i}", f"P{i}",
                              "2026-09-10T09:00", True, False, 0.0, "浦东"))
recs.append(ServiceRecord("R9", "药学诊查", "药学门诊", "H9", "P9",
                          "2026-09-10T09:00", False, True, 50.0, "浦东"))  # 虚收
recs.append(ServiceRecord("R10", "浓度测定", "血药浓度", "H1", "P1",
                          "2026-09-11T10:00", True, True, 120.0, "黄浦"))
rep = report(recs, INST)
print("  开展:", json.dumps(rep["开展"]["分项目"], ensure_ascii=False))
print("  未见开展的类别:", rep["开展"]["未见开展的类别"])
print("  漏收:", rep["漏收"]["漏收笔数"], "笔 →", rep["漏收"]["去向"])
print("  虚收待核:", rep["虚收待核"]["待核笔数"], "笔 /", rep["虚收待核"]["待核金额"],
      "元 |", rep["虚收待核"]["判定"])
for p in ["评估开展情况并反馈定价", "对开展率低的机构核减预算"]:
    try:
        assert_not_suppression(p); print(f"  ✓ 用途「{p}」放行")
    except EvaluationMisuse as e: print(f"  ✓ 用途「{p}」拒绝：{str(e)[:72]}")

print("\n=== 2 工作记录规范化 ===")
good = WorkRecord("W1", "药学门诊", "PT-a", "P1", "2026-09-10T09:00",
                  "2026-09-10T09:40", "2026-09-10T11:00",
                  {"服务对象": "PT-a", "服务时间": "09:00", "用药史采集": "5种",
                   "评估结论": "存在重复用药", "干预建议": "停用其一", "执行药师": "P1"})
late = WorkRecord("W2", "药学门诊", "PT-b", "P1", "2026-09-10T09:00",
                  "2026-09-12T18:00", "2026-09-10T11:00",
                  {"服务对象": "PT-b", "服务时间": "09:00", "用药史采集": "3种",
                   "评估结论": "无", "干预建议": "无", "执行药师": "P1"})
thin = WorkRecord("W3", "药学门诊", "PT-c", "P2", "2026-09-10T09:00",
                  "2026-09-10T09:20", None, {"服务对象": "PT-c", "服务时间": "09:00"})
for w in (good, late, thin):
    a = authenticity_signal(w)
    print(f"  {w.record_id}: {a['真实性']} | {a['要素']} | {a['时间链']}")
    if a["问题"]: print("      ", a["问题"][0][:76])
burst = [WorkRecord(f"B{i}", "药学门诊", f"PT-{i}", "P7", "2026-09-10T09:00",
                    "2026-09-12T23:11", None, {}) for i in range(7)]
bf = check_backfill(burst + [good])
print("  批量补录:", bf["批量补录疑似"], "组 |", bf["阈值"],
      "| 条数", bf["明细"][0]["条数"] if bf["明细"] else "-")
try: assert_not_quality_score("用于药师绩效考核排名")
except RecordRejected as e: print("  ✓ 拒绝用作绩效:", str(e)[:70])

print("\n=== 3 智能体评价机制 ===")
for m in ["拦截金额", "年度节省金额", "拒付率"]:
    try: assert_metric_allowed(m); print(f"  ✗ {m} 未拦截")
    except MetricRefused as e: print(f"  ✓ 「{m}」拒绝：{str(e)[:62]}")
# 全阴性模型：原始一致率高但 kappa=0
ph = [1]*5 + [0]*95
allneg = [0]*100
g1 = deployment_gate(allneg, ph)
print(f"  全阴性模型: 原始一致率 {g1['原始一致率']} / kappa {g1['kappa']} → {g1['闸门']}")
# 有判别力但样本量不足
agent = [1]*36 + [0]*4 + [1]*6 + [0]*54
truth = [1]*36 + [1]*4 + [0]*6 + [0]*54
g2 = deployment_gate(agent, truth)
print(f"  有判别力: kappa {g2['kappa']} / 检出率 {g2['检出率']} "
      f"(下界 {g2['检出率95CI下界']}) → {g2['闸门']}")
print("  双盲设计常见错误:", blinded_review_design(400)["常见错误"][:74])

print("\n=== 4 知识库标准：超说明书用药 ===")
es = [admit(Entry("K1", "药品说明书", "某药说明书 2024 版",
                  "适应症不含本用法", "2024-01-01")),
      admit(Entry("K2", "临床诊疗指南", "CSCO 指南 2025 版 第4.2节",
                  "推荐用于二线治疗", "2025-01-01", evidence_grade="2A"))]
# 说明书沉默 + 指南支持
es2 = [es[1]]
r = resolve(es2, "2026-03-01")
print("  说明书未载明而有指南支持 →", r["立场"], "| 可否据此定违规:", r["可否据此定违规"])
print("   ", r["处置"], "|", r["依据"])
ok = off_label_pathway({"无更好治疗手段": True, "患者知情同意": True,
                        "循证医学证据": True, "机构审核制度": True})
print("  四条件齐备:", ok["临床合法性"])
print("    支付立场:", ok["支付立场"][:62])
ng = off_label_pathway({"无更好治疗手段": True, "循证医学证据": True})
print("  缺两条件:", ng["临床合法性"], "| 未满足", ng["未满足"])
try: as_of(es, "2023-06-01")
except KnowledgeRejected as e: print("  ✓ as-of 拒绝回退:", str(e)[:68])
try: assert_not_blanket_denial("凡超说明书用药一律拒付")
except KnowledgeRejected as e: print("  ✓ 拒绝整体封杀:", str(e)[:76])
try: admit(Entry("K9", "临床诊疗指南", "综合整理", "x", "2025-01-01"))
except KnowledgeRejected as e: print("  ✓ 拒绝不可溯来源:", str(e)[:62])
