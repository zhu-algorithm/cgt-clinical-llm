import json
from fmed_rx.checks import check_overdose, check_duplicate, check_off_label, triage

# 案例一：山东一附院通报的真实情形——格列美脲超说明书每日最大剂量 6mg
case1 = [dict(drug="格列美脲", n_units=30, strength_mg=2.0, days=30)]   # 2mg*30片/30天 = 2mg/日 合规
case2 = [dict(drug="格列美脲", n_units=60, strength_mg=4.0, days=30)]   # 8mg/日 超量

# 案例三：归集陷阱——单方+复方各一张，逐方看都不超，合并看超量
case3 = [dict(drug="格列本脲", n_units=30, strength_mg=5.0, days=30),
         dict(drug="二甲双胍格列本脲片(II)", n_units=60, strength_mg=None, days=30)]

# 案例四：商品名不同、成分相同
case4 = [dict(drug="亚莫利", n_units=30, strength_mg=2.0, days=30),
         dict(drug="万苏平", n_units=30, strength_mg=2.0, days=30)]

# 案例五：同类重复——两种ARB并用
case5 = [dict(drug="缬沙坦", n_units=28, strength_mg=80.0, days=28),
         dict(drug="氯沙坦钾", n_units=28, strength_mg=50.0, days=28)]

for name, rx in [("①格列美脲2mg/日", case1), ("②格列美脲8mg/日", case2),
                 ("③单方+复方归集", case3), ("④商品名不同同成分", case4),
                 ("⑤两种ARB并用", case5)]:
    od = check_overdose(rx, 30)
    du = check_duplicate(rx)
    ol = check_off_label(rx, ["2型糖尿病"])
    print(f"\n=== {name} ===")
    for o in od:
        if o["判定"] != "合规":
            print("  超量:", {k: v for k, v in o.items() if k != "来源"})
        else:
            print(f"  合规: {o['成分']} {o['日均mg']}mg/日 ≤ {o['上限mg']}")
    for d in du:
        print("  重复:", d)
    print("  分级:", triage(od, du, ol))
