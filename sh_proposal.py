"""上海医保控费测算 —— 可提交版。

一手口径
  上海市医保局《2024/2023年度上海市基本医疗保险信息披露公告》
  国家医保局令第7号《医疗保障基金使用监督管理条例实施细则》（2026-04-01 施行）
  国家医保局《实施细则》新闻发布会（2026-03-31）：《条例》近五年累计追回约1200亿元，
    其中智能监管挽回基金损失95亿元
  天津健共体：按人头总额打包、结余留用超支不补，结余率按口径 16%–31%（机构口径）/
    约17.31%（卫健委口径）/ 超20%（2023门特糖尿病）/ 超25%（招股书18个月人均额度）

标注规则  OFFICIAL = 已公布实际值    DERIVED = 由 OFFICIAL 算出    ASSUMPTION = 本模型设定
"""
import json
import pandas as pd

SH = {
    2023: dict(zg_in=2222.1, zg_out=1264.6, zg_enrol=1623.2,
               jm_in=104.0, jm_out=118.3, jm_enrol=380.9),
    2024: dict(zg_in=2048.5, zg_out=1545.2, zg_enrol=1631.6,
               jm_in=122.8, jm_out=122.6, jm_enrol=388.9),
}
TJ_ZG_SURPLUS_RATE_2023 = 0.277        # OFFICIAL 天津职工医保当期结余率
NATIONAL_SURPLUS_RATE_2023 = 0.226     # OFFICIAL 全国平均
NAT_RECOVERED_5Y = 1200.0              # OFFICIAL 累计追回（亿元）
NAT_SMART_5Y = 95.0                    # OFFICIAL 其中智能监管挽回（亿元）
NAT_FUND_OUT_2024 = 29700.0            # 约数，用于比率标定


def surplus_rates():
    r = {}
    for y in (2023, 2024):
        d = SH[y]
        r[y] = {
            "职工当期结余率": (d["zg_in"] - d["zg_out"]) / d["zg_in"],
            "居民当期结余率": (d["jm_in"] - d["jm_out"]) / d["jm_in"],
            "职工支出_亿": d["zg_out"], "居民支出_亿": d["jm_out"],
        }
    return r


def capitation_applicability():
    """天津模式（按人头打包 + 结余留用）需要基金有结余空间。
    逐险种判断上海是否具备该空间。"""
    s = surplus_rates()
    rows = []
    for y in (2023, 2024):
        for k, label in (("职工当期结余率", "职工医保"), ("居民当期结余率", "城乡居民医保")):
            rate = s[y][k]
            rows.append({
                "年度": y, "险种": label, "当期结余率": round(rate, 4),
                "对比天津2023职工27.7%": round(rate - TJ_ZG_SURPLUS_RATE_2023, 4),
                "结余留用模式可行性": (
                    "可行——结余空间不低于天津" if rate >= TJ_ZG_SURPLUS_RATE_2023 else
                    "可行但空间小于天津" if rate >= 0.10 else
                    "不适用——无结余空间，主责是保基本而非赌结余"),
            })
    return pd.DataFrame(rows)


def savings_envelope():
    base_zg, base_jm = SH[2024]["zg_out"], SH[2024]["jm_out"]
    base = base_zg + base_jm
    smart_share = NAT_SMART_5Y / NAT_RECOVERED_5Y          # DERIVED 7.9%
    nat_rec_rate_yr = 275.0 / NAT_FUND_OUT_2024            # OFFICIAL 2024 追回率 0.93%

    L = [
        dict(杠杆="① 智能审核（《实施细则》第五条法定职责）",
             依据=f"OFFICIAL 智能监管占累计追回 {smart_share:.1%}；2024全国追回率 {nat_rec_rate_yr:.2%}",
             低=base * nat_rec_rate_yr * smart_share * 0.6,
             中=base * nat_rec_rate_yr * smart_share * 1.0,
             高=base * nat_rec_rate_yr * smart_share * 2.0,
             备注="上海智能监管起步早，存量已覆盖，边际增益取 0.6–2.0 倍区间"),
        dict(杠杆="② 全口径基金监管追回（含飞检/自查/协议处理）",
             依据=f"OFFICIAL 2024全国追回275亿/基金支出约2.97万亿={nat_rec_rate_yr:.2%}",
             低=base * nat_rec_rate_yr * 0.6, 中=base * nat_rec_rate_yr,
             高=base * nat_rec_rate_yr * 1.4,
             备注="主体为常规稽核；本项目贡献是提高线索命中率，不替代稽核"),
        dict(杠杆="③ 职工医保支出增速管理（每降1个百分点）",
             依据="OFFICIAL 上海职工医保支出 2023→2024 增长 22.2%（1264.6→1545.2亿）",
             低=base_zg * 0.005, 中=base_zg * 0.010, 高=base_zg * 0.020,
             备注="按降 0.5/1/2 个百分点；逐年复利，是最大单项杠杆"),
        dict(杠杆="④ 慢病按人头打包 + 结余留用（天津模式，仅限职工医保）",
             依据="ASSUMPTION 以天津门特糖尿病结余率区间折算，上海慢病门特支出占比按10%设",
             低=base_zg * 0.10 * 0.1731 * 0.3,
             中=base_zg * 0.10 * 0.20 * 0.5,
             高=base_zg * 0.10 * 0.25 * 0.7,
             备注="天津结余率按口径为16%–31%，此处取17.31%/20%/25%；再乘可及人群渗透率0.3/0.5/0.7"),
    ]
    df = pd.DataFrame(L)
    for c in ("低", "中", "高"):
        df[c] = df[c].round(2)
    df["占2024基金支出"] = (df["中"] / base * 100).round(3).astype(str) + "%"
    return df, base


def non_additive_total(df, base):
    """杠杆之间高度重叠：智能审核命中的违规与飞检追回的违规是同一批钱，
    增速管理也部分由前两者实现。直接相加会严重高估。"""
    mid = df["中"].sum()
    overlap = 0.45          # ASSUMPTION 重叠扣减比例
    adj = mid * (1 - overlap)
    return {"杠杆中值直接相加_亿": round(mid, 2),
            "重叠扣减比例": overlap,
            "扣减后年化_亿": round(adj, 2),
            "占2024基金支出": f"{adj / base * 100:.2f}%"}


if __name__ == "__main__":
    s = surplus_rates()
    cap = capitation_applicability()
    env, base = savings_envelope()
    tot = non_additive_total(env, base)

    print("【一】上海基金结余空间（OFFICIAL）")
    for y in (2023, 2024):
        print(f"  {y}  职工当期结余率 {s[y]['职工当期结余率']:.1%}   "
              f"居民当期结余率 {s[y]['居民当期结余率']:+.1%}")
    print(f"  参照：天津2023职工 {TJ_ZG_SURPLUS_RATE_2023:.1%}，全国平均 {NATIONAL_SURPLUS_RATE_2023:.1%}")

    print("\n【二】天津「结余留用」模式在上海的适用性")
    print(cap.to_string(index=False))

    print(f"\n【三】控费杠杆（亿元/年，2024基金支出基数 {base:.1f} 亿）")
    print(env[["杠杆", "低", "中", "高", "占2024基金支出"]].to_string(index=False))

    print("\n【四】合计（已扣除杠杆重叠）")
    print(json.dumps(tot, ensure_ascii=False, indent=2))

    json.dump({"surplus_rates": s, "envelope": env.to_dict(orient="records"),
               "applicability": cap.to_dict(orient="records"), "total": tot,
               "base_2024_total_out": base}, open("sh_proposal.json", "w"),
              ensure_ascii=False, indent=2, default=float)
    env.to_csv("sh_savings_envelope.csv", index=False)
    cap.to_csv("sh_capitation_applicability.csv", index=False)
