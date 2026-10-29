import json
from fmed_hv.coding_recovery import CodingGap, Evidence, batch as cr_batch, audit_direction, assess, UpcodingRefused
from fmed_hv.risk_shield import pre_order_check, adoption_metrics, doctor_exposure
from fmed_hv.appeal_engine import Denial, batch as ap_batch, quality_signal
from fmed_hv.drg_ops import group_margin, dumping_watch, special_case_quota, quality_counterweight
from fmed_hv.drg_api import authorize, assert_flow, view, audit_separation, ScopeDenied, PAYER, HEALTH, HOSPITAL

print("=== 1 编码回补：硬凭据闸门 ===")
gaps = [
 CodingGap("C1","ES31",8200.0,"ES33",11600.0,"并发症","急性肾损伤",
           [Evidence("检验报告","LAB-77012","肌酐 286 μmol/L"), Evidence("医嘱明细","ORD-5521","连续肾替代治疗")]),
 CodingGap("C2","GZ15",6100.0,"GZ13",9400.0,"合并症","2型糖尿病伴酮症",
           [Evidence("入院记录叙述","ADM-3","既往糖尿病史")]),   # 仅叙述 → 应拒
 CodingGap("C3","BR29",15200.0,"BR25",14100.0,"手术操作","经皮穿刺",[]),
]
r = cr_batch(gaps)
print("  可回补:", r["可回补笔数"], "笔 /", r["可回补金额合计"], "元")
for x in r["可回补"]: print("   ", json.dumps(x, ensure_ascii=False)[:150])
print("  因无硬凭据被拒:", r["被拒笔数"], "笔，潜在金额", r["被拒潜在金额"], "元")
print("   ", r["因无硬凭据被拒"][0]["拒绝原因"][:110])
print("  方向性审计:", json.dumps(audit_direction(gaps), ensure_ascii=False))

print("\n=== 2 风险护盾：超时 fail-open ===")
import time
fast = [("超量用药", lambda o: dict(严重度=3, 详情="日剂量 8mg > 上限 6mg"))]
slow = [("慢检查", lambda o: (time.sleep(1.2), None)[1])]
print("  正常:", json.dumps(pre_order_check({"drug":"格列美脲"}, fast), ensure_ascii=False)[:150])
print("  超时:", json.dumps(pre_order_check({"drug":"X"}, slow, sla_ms=300), ensure_ascii=False)[:190])
hist=[{"级别":"建议改方","已改方":True,"命中":[{"检查":"超量用药"}]},
      {"级别":"建议改方","已改方":False,"命中":[{"检查":"重复用药"}]},
      {"级别":"通过","超时":True}]
print("  采纳/超时:", json.dumps(adoption_metrics(hist), ensure_ascii=False)[:180])

print("\n=== 3 申诉引擎：反游戏约束 ===")
ds=[Denial("A1",12400,"R-0712","限既往一线治疗失败后使用","2024版","2024-06-11",
           [("历史结算","奥沙利铂 6 周期","SETL-8891"),("LIS","CEA 42.1 ng/mL","LAB-2210")],0.62),
    Denial("A2",8800,"R-0415","限晚期","2024版","2024-07-02",[],None),
    Denial("A3",5300,"R-0902","限特定检验值","2024版","2024-05-20",
           [("LIS","HbA1c 6.1%","LAB-5567")],0.41)]
res = ap_batch(ds, {"A1":"VERIFIED","A2":"UNVERIFIED","A3":"CONTRADICTED"})
print("  可申诉:", res["可申诉"], "笔 /", res["可申诉金额"], "元")
print("  拒付成立不申诉:", res["拒付成立"], "笔 /", res["拒付成立金额"], "元")
for x in res["不申诉清单"]: print("   ", x["case_id"], x["原因"][:88])

print("\n=== 4 DRG 运营：推诿监测 ===")
t0=[dict(drg="ES31",payment=8200,cost=9900,is_severe=True,weight=2.1) for _ in range(40)]
t0+=[dict(drg="GZ15",payment=6100,cost=4200,is_severe=False,weight=0.9) for _ in range(60)]
m0=group_margin(t0)
t1=[dict(drg="ES31",payment=8200,cost=9900,is_severe=True,weight=2.1) for _ in range(26)]
t1+=[dict(drg="GZ15",payment=6100,cost=4200,is_severe=False,weight=0.9) for _ in range(66)]
m1=group_margin(t1)
print("  病组盈亏:", json.dumps(m0, ensure_ascii=False)[:210])
print("  推诿监测:", json.dumps(dumping_watch(m0,m1), ensure_ascii=False)[:230])
print("  质量配重:", json.dumps(quality_counterweight(t1), ensure_ascii=False)[:150])
print("  特例单议:", json.dumps(special_case_quota(10000,480,"DRG"), ensure_ascii=False)[:160])

print("\n=== 5 DRG 双方接口：按用途分区 ===")
print(" ", json.dumps(audit_separation(), ensure_ascii=False))
rec=dict(group_code="ES31",group_weight=2.1,payment_amount=8200,upcoding_flag=False,
         denial_flag=True,denial_amount=1200,violation_score=0.72,
         cmi=2.05,level4_surgery_rate=0.31,low_risk_mortality=0.0004,case_count=40,avg_los=9.2)
for subj in (PAYER,HEALTH,HOSPITAL):
    v=view(subj,rec)
    print(f"  {subj}: 可见 {len(v['记录'])} 项，屏蔽 {v['已屏蔽']}")
for src,dst,fields,lab in [(PAYER,HEALTH,["denial_flag","violation_score"],"拒付信号→卫健委"),
                           (HEALTH,PAYER,["cmi","low_risk_mortality"],"质量评分→医保局")]:
    try:
        assert_flow(src,dst,fields); print(f"  ✗ {lab} 未拦截")
    except ScopeDenied as e: print(f"  ✓ {lab}: {str(e)[:96]}")
try:
    authorize(HEALTH,["cmi","denial_amount"]); print("  ✗ 越权未拦截")
except ScopeDenied as e: print("  ✓ 卫健委索取拒付金额:", str(e)[:86])

print("\n=== 6 fail-open 回归 ===")
import time as _t
slow=[("慢检查", lambda o:(_t.sleep(1.2),None)[1]),
      ("快检查", lambda o: dict(严重度=3,详情="超量"))]
t0=_t.perf_counter(); r=pre_order_check({"drug":"X"},slow,sla_ms=300)
wall=(_t.perf_counter()-t0)*1000
print(f"  墙钟 {wall:.0f}ms（SLA 300ms）→ 级别={r['级别']} 超时={r['超时']} 弃用={r.get('弃用检查')}")
assert r["超时"] is True, "超时未标记"
assert r["级别"]=="通过", "超时却阻断了医嘱"
assert wall < 900, f"未按时放行，等了 {wall:.0f}ms"
boom=[("会抛异常", lambda o:(_ for _ in ()).throw(RuntimeError("db down")))]
r2=pre_order_check({"drug":"Y"},boom,sla_ms=300)
print("  检查器异常 → 级别=%s 超时=%s 弃用=%s" % (r2["级别"],r2["超时"],r2.get("弃用检查")))
assert r2["级别"]=="通过", "异常却阻断了医嘱"
print("  ✓ 慢检查被弃用而非等完；异常同样放行")
