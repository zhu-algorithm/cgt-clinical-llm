"""Turn raw ClinicalTrials.gov records into the structured clinical-management corpus.

Splits eligibility prose into inclusion / exclusion item lists, tags indication class,
and extracts the safety / DLT / statistical-design passages that the clinical
management sub-model needs to learn.
"""
import json, re, collections

INC_HDR = re.compile(r"(?im)^\s*[\-\*\u2022]?\s*(inclusion\s*criteria|key\s*inclusion|eligible\s*(patients|subjects)|纳入标准)\s*:?\s*$")
EXC_HDR = re.compile(r"(?im)^\s*[\-\*\u2022]?\s*(exclusion\s*criteria|key\s*exclusion|ineligib\w*|排除标准)\s*:?\s*$")
BULLET = re.compile(r"^\s*(?:[\-\*\u2022\u25cf\u25aa]|\(?\d{1,2}[\.\)]|[a-z][\.\)])\s*")

SAFETY_KW = re.compile(
    r"(dose[- ]limiting toxicity|DLT|cytokine release syndrome|CRS|ICANS|neurotoxicity|"
    r"adverse event|serious adverse event|SAE|CTCAE|graft[- ]versus[- ]host|GvHD|"
    r"maximum tolerated dose|MTD|treatment[- ]emergent)", re.I)
STAT_KW = re.compile(
    r"(sample size|statistical analysis|interim analysis|Bayesian|3\+3|BOIN|mTPI|"
    r"Simon'?s? two[- ]stage|power of|type I error|one[- ]sided|two[- ]sided|"
    r"Kaplan[- ]Meier|log[- ]rank|hazard ratio|confidence interval)", re.I)

INDICATION = {
    "hematologic": re.compile(r"(leukemia|lymphoma|myeloma|myelodysplastic|AML|ALL\b|CLL|DLBCL|MDS)", re.I),
    "solid_tumor": re.compile(r"(carcinoma|sarcoma|glioma|glioblastoma|melanoma|solid tumor|"
                              r"adenocarcinoma|mesothelioma|neuroblastoma|hepatocellular)", re.I),
    "autoimmune":  re.compile(r"(lupus|myositis|scleroderma|systemic sclerosis|multiple sclerosis|"
                              r"rheumatoid|Crohn|ulcerative colitis|myasthenia|autoimmune)", re.I),
    "gvhd_transplant": re.compile(r"(graft[- ]versus[- ]host|transplant)", re.I),
    "monogenic":   re.compile(r"(hemophilia|thalassemia|sickle cell|Duchenne|spinal muscular|"
                              r"cystic fibrosis|Fabry|Gaucher|retinitis pigmentosa)", re.I),
}


def split_eligibility(text):
    """Return (inclusion_items, exclusion_items). Falls back to a single bucket."""
    if not text:
        return [], []
    lines = text.splitlines()
    inc_at = exc_at = None
    for i, ln in enumerate(lines):
        if inc_at is None and INC_HDR.match(ln):
            inc_at = i
        elif EXC_HDR.match(ln):
            exc_at = i
            break
    def items(chunk):
        out, buf = [], ""
        for ln in chunk:
            s = ln.strip()
            if not s:
                continue
            if BULLET.match(ln) or (buf and len(buf) > 400):
                if buf.strip():
                    out.append(re.sub(r"\s+", " ", buf).strip())
                buf = BULLET.sub("", s)
            else:
                buf += " " + s
        if buf.strip():
            out.append(re.sub(r"\s+", " ", buf).strip())
        hdr = re.compile(r"(?i)^(inclusion|exclusion|key\s+(in|ex)clusion)\s*criteria\s*:?$|^(纳入|排除)标准[:：]?$")
        return [x for x in out if 8 <= len(x) <= 800 and not hdr.match(x.strip())]
    if inc_at is None and exc_at is None:
        return items(lines), []
    if exc_at is None:
        return items(lines[(inc_at or 0) + 1:]), []
    return items(lines[(inc_at or -1) + 1:exc_at]), items(lines[exc_at + 1:])


def classify(r):
    blob = " ".join(r.get("conditions") or []) + " " + (r.get("title") or "")
    return [k for k, pat in INDICATION.items() if pat.search(blob)] or ["other"]


def passages(r):
    """Safety and statistics passages lifted from free-text fields."""
    text = " ".join(filter(None, [r.get("detailed_description"), r.get("brief_summary")]))
    sents = re.split(r"(?<=[.;])\s+", text)
    safety = [s.strip() for s in sents if SAFETY_KW.search(s) and 40 <= len(s) <= 700]
    stats = [s.strip() for s in sents if STAT_KW.search(s) and 40 <= len(s) <= 700]
    # outcome measures mentioning safety are also signal
    for o in (r.get("primary_outcomes") or []) + (r.get("secondary_outcomes") or []):
        m = f"{o.get('measure') or ''} {o.get('description') or ''}".strip()
        if m and SAFETY_KW.search(m) and len(m) <= 700:
            safety.append(re.sub(r"\s+", " ", m))
    return safety[:12], stats[:8]


def build(raw_path="raw/ctgov_raw.json", out="data/ctgov_structured.jsonl"):
    store = json.load(open(raw_path, encoding="utf-8"))
    rows, agg = [], collections.Counter()
    for r in store.values():
        inc, exc = split_eligibility(r.get("eligibility_criteria"))
        safety, stats = passages(r)
        rows.append({
            "nct_id": r["nct_id"], "title": r["title"], "status": r["status"],
            "phase": r.get("phase"), "start_date": r.get("start_date"),
            "sponsor": r.get("sponsor"), "enrollment": r.get("enrollment"),
            "modalities": r["modalities"], "indication_class": classify(r),
            "conditions": r.get("conditions"),
            "interventions": r.get("interventions"),
            "inclusion_criteria": inc, "exclusion_criteria": exc,
            "primary_outcomes": r.get("primary_outcomes"),
            "secondary_outcomes": r.get("secondary_outcomes"),
            "design": {k: r.get(k) for k in
                       ("design_allocation", "design_model", "design_masking", "design_purpose")},
            "min_age": r.get("min_age"), "max_age": r.get("max_age"), "sex": r.get("sex"),
            "safety_passages": safety, "statistical_passages": stats,
            "brief_summary": r.get("brief_summary"),
        })
        agg["with_inclusion"] += bool(inc)
        agg["with_exclusion"] += bool(exc)
        agg["with_safety"] += bool(safety)
        agg["with_stats"] += bool(stats)
    with open(out, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    agg["n_inclusion_items"] = sum(len(x["inclusion_criteria"]) for x in rows)
    agg["n_exclusion_items"] = sum(len(x["exclusion_criteria"]) for x in rows)
    agg["n_safety_passages"] = sum(len(x["safety_passages"]) for x in rows)
    agg["n_stat_passages"] = sum(len(x["statistical_passages"]) for x in rows)
    agg["trials"] = len(rows)
    return dict(agg), rows
