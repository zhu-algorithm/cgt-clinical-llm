"""Fetch the cell-and-gene-therapy / advanced-modality trial corpus from
ClinicalTrials.gov API v2.  Public domain data (US government).

Writes raw/ctgov_raw.json : {nct_id: {flat record}}
"""
import json, time, pathlib
import requests

API = "https://clinicaltrials.gov/api/v2/studies"

FIELDS = [
    "NCTId", "BriefTitle", "OverallStatus", "Phase", "StudyType", "StartDate",
    "Condition", "InterventionName", "InterventionType", "LeadSponsorName",
    "EnrollmentCount", "EligibilityCriteria", "MinimumAge", "MaximumAge", "Sex",
    "HealthyVolunteers",
    "PrimaryOutcomeMeasure", "PrimaryOutcomeTimeFrame", "PrimaryOutcomeDescription",
    "SecondaryOutcomeMeasure", "SecondaryOutcomeTimeFrame",
    "DesignAllocation", "DesignInterventionModel", "DesignMasking",
    "DesignPrimaryPurpose", "DetailedDescription", "BriefSummary", "LocationCountry",
]

MODALITIES = {
    "CAR-T":          '"CAR-T" OR "chimeric antigen receptor"',
    "allogeneic_CAR": '"allogeneic CAR" OR "universal CAR" OR "off-the-shelf CAR"',
    "in_vivo_CAR":    '"in vivo CAR"',
    "CAR-NK":         '"CAR-NK" OR "CAR natural killer"',
    "TCR-T":          '"TCR-T" OR "T cell receptor engineered" OR "TCR engineered"',
    "TIL":            '"tumor infiltrating lymphocyte"',
    "MSC":            '"mesenchymal stem cell" OR "mesenchymal stromal cell"',
    "gene_therapy":   '"gene therapy" OR "AAV vector" OR "lentiviral vector"',
    "RNA_drug":       '"siRNA" OR "antisense oligonucleotide" OR "mRNA therapeutic"',
    "cyclic_peptide": '"cyclic peptide" OR "macrocyclic peptide"',
    "oncolytic":      '"oncolytic virus"',
}


def _get(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def flatten(study):
    p = study.get("protocolSection", {})
    ident, status = p.get("identificationModule", {}), p.get("statusModule", {})
    design, elig = p.get("designModule", {}), p.get("eligibilityModule", {})
    arms = p.get("armsInterventionsModule", {})
    outs, desc = p.get("outcomesModule", {}), p.get("descriptionModule", {})
    cond = p.get("conditionsModule", {})
    di = design.get("designInfo", {}) or {}
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "start_date": _get(status, "startDateStruct", "date"),
        "phase": (design.get("phases") or []),
        "study_type": design.get("studyType"),
        "enrollment": _get(design, "enrollmentInfo", "count"),
        "sponsor": _get(p, "sponsorCollaboratorsModule", "leadSponsor", "name"),
        "conditions": cond.get("conditions") or [],
        "interventions": [
            {"type": i.get("type"), "name": i.get("name")}
            for i in (arms.get("interventions") or [])
        ],
        "eligibility_criteria": elig.get("eligibilityCriteria"),
        "min_age": elig.get("minimumAge"),
        "max_age": elig.get("maximumAge"),
        "sex": elig.get("sex"),
        "healthy_volunteers": elig.get("healthyVolunteers"),
        "primary_outcomes": [
            {"measure": o.get("measure"), "time_frame": o.get("timeFrame"),
             "description": o.get("description")}
            for o in (outs.get("primaryOutcomes") or [])
        ],
        "secondary_outcomes": [
            {"measure": o.get("measure"), "time_frame": o.get("timeFrame")}
            for o in (outs.get("secondaryOutcomes") or [])
        ],
        "design_allocation": di.get("allocation"),
        "design_model": di.get("interventionModel"),
        "design_masking": _get(di, "maskingInfo", "masking"),
        "design_purpose": di.get("primaryPurpose"),
        "brief_summary": desc.get("briefSummary"),
        "detailed_description": desc.get("detailedDescription"),
    }


def fetch(out_path="raw/ctgov_raw.json", page_size=500, sleep=0.3):
    pathlib.Path("raw").mkdir(exist_ok=True)
    store, tally = {}, {}
    for mod, expr in MODALITIES.items():
        token, got = None, 0
        while True:
            params = {"query.intr": expr, "fields": ",".join(FIELDS),
                      "pageSize": page_size, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
            if token:
                params["pageToken"] = token
            r = requests.get(API, params=params, timeout=120)
            r.raise_for_status()
            d = r.json()
            for s in d.get("studies", []):
                rec = flatten(s)
                nid = rec["nct_id"]
                if not nid:
                    continue
                if nid in store:
                    store[nid]["modalities"].append(mod)
                else:
                    rec["modalities"] = [mod]
                    store[nid] = rec
                got += 1
            token = d.get("nextPageToken")
            time.sleep(sleep)
            if not token:
                break
        tally[mod] = got
        print(f"{mod:16s} fetched={got} cumulative_unique={len(store)}", flush=True)
    json.dump(store, open(out_path, "w"), ensure_ascii=False)
    json.dump(tally, open("raw/ctgov_tally.json", "w"), indent=2)
    return tally, len(store)


if __name__ == "__main__":
    tally, n = fetch()
    print("TOTAL UNIQUE:", n)
