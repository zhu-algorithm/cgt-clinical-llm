"""国家医保基金智能监管「两库」摄取器。

来源：国家医保局《国家医保基金智能监管知识库、规则库（2025年版）》，
      知识点明细按批次以 PDF + XLSX 公开（截至本次已发布第二十一批）。
      各省级医保部门须据此对省级平台智能监管子系统「两库」动态更新。

设计立场：F-Med 的规则层**原生消费官方格式**，不自建平行规则体系。
  理由有二：
  (1) 官方库免费、权威、且省级平台有同步义务——自建平行库既重复投入，
      又会在判定口径上与省平台产生分歧，分歧本身就是审核风险；
  (2) 规则库既然是公共品，差异化就不在规则库，而在规则库表达不了的那一层
      （自然语言支付范围限定 → 自然语言病历的适用判定）。
"""
from __future__ import annotations
import pandas as pd

CANON = {
    "序号": "seq",
    "诊断代码\n（ICD-10医保2.0版）": "icd_code",
    "诊断名称\n（ICD-10医保2.0版）": "icd_name",
    "限定性别": "required_sex",
    "检出逻辑": "detect_logic",
    "逻辑依据": "basis",
}


def load_batch(xlsx_path: str) -> pd.DataFrame:
    """读取一批知识点明细。表头位置随批次浮动，按列名定位而非按固定行号。"""
    raw = pd.read_excel(xlsx_path, header=None)
    hdr = None
    for i in range(min(12, len(raw))):
        row = [str(c).strip() for c in raw.iloc[i].tolist()]
        if "序号" in row:
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"未定位到表头行：{xlsx_path}")
    df = pd.read_excel(xlsx_path, header=hdr)
    df.columns = [str(c).strip() for c in df.columns]
    ren = {c: CANON[c] for c in df.columns if c in CANON}
    df = df.rename(columns=ren)
    df = df.dropna(subset=["icd_code"]) if "icd_code" in df else df
    df = df[~df["seq"].astype(str).str.contains("序号", na=False)]
    return df.reset_index(drop=True)


def build_sex_rule(df: pd.DataFrame) -> dict:
    """诊断-性别约束表：icd_code -> 限定性别"""
    out = {}
    for _, r in df.iterrows():
        code = str(r["icd_code"]).strip()
        sex = str(r["required_sex"]).strip()
        if sex in ("男", "女"):
            out[code] = sex
    return out


def check_diagnosis_sex(diagnoses, patient_sex: str, rule: dict):
    """检出「诊断与患者性别不符」。

    诊断代码可能带星剑号（如 A06.800x004+N51.2*），需按主码与全码双向匹配。
    """
    hits = []
    for d in diagnoses:
        d = str(d).strip()
        req = rule.get(d)
        if req is None:
            # 主码回退匹配：去掉 +附加码
            primary = d.split("+")[0]
            for code, s in rule.items():
                if code.split("+")[0] == primary:
                    req = s
                    break
        if req and req != patient_sex:
            hits.append(dict(诊断=d, 限定性别=req, 患者性别=patient_sex,
                             检出="诊断与患者性别不符",
                             依据="国家医保基金智能监管知识库、规则库（2025年版）"))
    return hits
