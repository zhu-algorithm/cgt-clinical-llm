import json, sys, pathlib
sys.path.insert(0, ".")
from fmed_gate import new_identity, build, review, run_invariants
from fmed_gate.envelope import SignatureInvalid, verify, seal, unseal
from fmed_gate.schema import ContributionRejected
from fmed_gate.tiers import TierRefused, classify

ident = json.loads(pathlib.Path("demo_id.json").read_text()) if pathlib.Path("demo_id.json").exists() else new_identity()
pathlib.Path("demo_id.json").write_text(json.dumps(ident))
trusted = json.loads(pathlib.Path("gate/trusted_keys.json").read_text(encoding="utf-8"))
trusted["demo-agent"]["public_key"] = ident["public_key"]
pathlib.Path("gate/trusted_keys.json").write_text(json.dumps(trusted, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== 1 不变量基线（已恢复模块）===")
r = run_invariants(".")
print(f"  {r['结论']} | 已检查 {r['已检查']} | 失败 {r['失败数']}")
for x in r["失败"][:5]: print("   ✗", x["文件"], x["问题"])

print("\n=== 2 T0 合法贡献：补一条法规条文 ===")
ok = dict(类型="法规条文", 内容=dict(
    标题="药学类医疗服务价格项目立项指南（试行）", 发文机关="国家医疗保障局",
    文号="（试行）", 施行日期="2026-02-12", 条文号="全文·编制说明",
    条文原文="将已有价格项目规范整合为10项，设立药学诊查、集中配置、浓度测定、中药药学服务等4类10项价格项目。",
    来源URL="http://www.nhsa.gov.cn/art/2026/2/11/art_14_19656.html"))
env = build(ok, "demo-agent", ident["signing_key"], "T0", "补充药学类价格项目立项依据")
print(" ", json.dumps(review(env, ["corpus/nhsa/药学类立项指南.json"]), ensure_ascii=False)[:290])

print("\n=== 3 拦截测试 ===")
cases = []
# a 未登记公钥
other = new_identity()
cases.append(("未登记智能体", build(ok, "rogue-agent", other["signing_key"], "T0", "x"), ["corpus/a.json"]))
# b 签名后被改动
tampered = build(ok, "demo-agent", ident["signing_key"], "T0", "x")
tampered["body"]["payload"]["内容"]["条文原文"] = "整合为100项"
cases.append(("内容被篡改", tampered, ["corpus/a.json"]))
# c 患者级数据
phi = dict(类型="知识点", 内容=dict(名称="示例", 判定逻辑="x", 逻辑依据="y", 来源="卫健委",
            适用版本="2026", 备注="患者 310101199003074512，病案号: 88213"))
cases.append(("患者级数据", build(phi, "demo-agent", ident["signing_key"], "T0", "x"), ["corpus/a.json"]))
# d 来源为模型生成
gen = dict(类型="知识点", 内容=dict(名称="示例", 判定逻辑="x", 逻辑依据="综合整理",
            来源="模型生成", 适用版本="2026"))
cases.append(("来源不可溯", build(gen, "demo-agent", ident["signing_key"], "T0", "x"), ["corpus/a.json"]))
# e 自报级别不符：改流程却报 T0
flow = dict(类型="流程变更", 内容=dict(目标模块="risk_shield", 变更说明="改为超时阻断",
            风险评估="无", 不变量影响="无", 回滚方案="revert"))
cases.append(("改流程自报T0", build(flow, "demo-agent", ident["signing_key"], "T0", "x"), ["fmed_hv/risk_shield.py"]))
# f 触及受保护文件
prot = dict(类型="规则", 内容=dict(规则ID="R1", 触发条件="x", 处置建议="y",
            依据条文="《条例》第三十八条", 误报代价说明="低"))
cases.append(("触及红线文件", build(prot, "demo-agent", ident["signing_key"], "T1", "x"), ["fmed_rx/scope_guard.py"]))

for label, e, paths in cases:
    try:
        review(e, paths); print(f"  ✗ {label}：未拦截")
    except Exception as ex:
        print(f"  ✓ {label}：{type(ex).__name__} — {str(ex)[:92]}")

print("\n=== 4 定向加密（仅所有者可解）===")
from nacl.public import PrivateKey
from fmed_gate.envelope import b64
owner = PrivateKey.generate()
ct = seal("仅所有者可见的内部评估口径".encode(), b64(bytes(owner.public_key)))
print("  密文长度", len(ct), "| 所有者解密:", unseal(ct, b64(bytes(owner))).decode())
try:
    unseal(ct, b64(bytes(PrivateKey.generate())))
    print("  ✗ 他人竟可解密")
except Exception as ex:
    print(f"  ✓ 他人无法解密：{type(ex).__name__}")

print("\n=== 5 不变量被破坏时阻断 ===")
p = pathlib.Path("fmed_hv/risk_shield.py"); orig = p.read_text(encoding="utf-8")
p.write_text(orig.replace("ex.shutdown(wait=False)", "ex.shutdown(wait=True)"), encoding="utf-8")
r2 = run_invariants(".")
print(f"  改 wait=True 后：{r2['结论']}，失败 {r2['失败数']} 项")
if r2["失败"]: print("   ", r2["失败"][0]["说明"][:88])
p.write_text(orig, encoding="utf-8")
print("  已还原：", run_invariants(".")["结论"])
