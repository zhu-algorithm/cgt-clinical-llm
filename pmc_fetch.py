"""Build the CC-BY/CC0 CGT literature corpus from the PMC AWS Open Data bucket.

PMC retired its FTP bulk packages in Aug 2026; the datasets now live in
s3://pmc-oa-opendata (public, no login). Layout is flat per article-version:
    PMC<id>.<v>/PMC<id>.<v>.{json,txt,xml,pdf}
    metadata/PMC<id>.<v>.json      <- carries license_code

Stage A  esearch -> candidate PMCIDs for the CGT topic set
Stage B  parallel metadata fetch -> keep only commercially reusable licences
Stage C  parallel full-text (.txt) fetch for the kept set
"""
import gzip, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

for _k in ["all_proxy", "ALL_PROXY", "rsync_proxy", "RSYNC_PROXY", "ftp_proxy", "FTP_PROXY"]:
    os.environ.pop(_k, None)

S3 = "https://pmc-oa-opendata.s3.amazonaws.com/"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# licences that permit commercial reuse and derivatives
OK_LICENCES = {"CC0", "CC BY", "CCBY", "CC-BY", "PUBLIC DOMAIN", "CC BY-SA", "CC-BY-SA"}

TOPICS = [
    '("chimeric antigen receptor"[tiab] OR "CAR-T"[tiab])',
    '("cell therapy"[tiab] OR "cellular therapy"[tiab])',
    '("mesenchymal stem cell"[tiab] OR "mesenchymal stromal cell"[tiab])',
    '("gene therapy"[tiab] OR "AAV vector"[tiab] OR "lentiviral vector"[tiab])',
    '("siRNA"[tiab] OR "antisense oligonucleotide"[tiab] OR "mRNA vaccine"[tiab] OR "mRNA therapeutic"[tiab])',
    '("cyclic peptide"[tiab] OR "macrocyclic peptide"[tiab])',
    '("TCR-T"[tiab] OR "T cell receptor engineered"[tiab])',
]
TERM = "(" + " OR ".join(TOPICS) + ') AND "open access"[filter]'

_local = threading.local()


def sess():
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.mount("https://", requests.adapters.HTTPAdapter(
            pool_connections=64, pool_maxsize=64, max_retries=2))
        _local.s = s
    return _local.s


def _esearch_page(term, retstart, email=None):
    """One esearch page. NCBI caps retstart+retmax at 10000 and drops 'idlist'
    (no error field) past that, so callers must slice the query instead."""
    p = {"db": "pmc", "term": term, "retmax": 9999, "retstart": retstart,
         "retmode": "json"}
    if email:
        p["email"] = email
    r = requests.get(ESEARCH, params=p, timeout=180)
    r.raise_for_status()
    # NCBI occasionally emits raw control characters in the translation stack.
    res = json.loads(r.text, strict=False)["esearchresult"]
    return res.get("idlist", []), int(res["count"])


def stage_a(email=None, out="raw/pmc_ids.json", y0=1990, y1=2027):
    """Slice by publication year so no single slice exceeds the 10k page cap."""
    ids = set()
    pending = [(y, y) for y in range(y0, y1)]
    pending.append((0, y0 - 1))          # everything older, lumped
    while pending:
        lo, hi = pending.pop(0)
        span = f'("{lo}"[PubDate] : "{hi}"[PubDate])' if lo else f'("1800"[PubDate] : "{hi}"[PubDate])'
        term = f"({TERM}) AND {span}"
        batch, total = _esearch_page(term, 0, email)
        if total > 9999 and lo != hi:
            mid = (lo + hi) // 2         # split the span and retry
            pending = [(lo, mid), (mid + 1, hi)] + pending
            continue
        ids.update(batch)
        retstart = len(batch)
        while retstart < min(total, 9999):
            more, _ = _esearch_page(term, retstart, email)
            if not more:
                break
            ids.update(more)
            retstart += len(more)
            time.sleep(0.35)
        print(f"  {lo or '<'}–{hi}: total={total} got={len(batch)} cumulative={len(ids)}", flush=True)
        time.sleep(0.35)
    ids = sorted(ids)
    json.dump(ids, open(out, "w"))
    return ids


def _meta(pmcid):
    for v in (1, 2):
        try:
            r = sess().get(f"{S3}metadata/PMC{pmcid}.{v}.json", timeout=60)
            if r.status_code == 200:
                d = r.json()
                return pmcid, v, (d.get("license_code") or "").strip().upper(), d
        except Exception:
            pass
    return pmcid, None, None, None


def stage_b(ids, workers=48, out="raw/pmc_licences.json"):
    keep, seen, errs = {}, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_meta, i): i for i in ids}
        for f in as_completed(futs):
            pmcid, v, lic, d = f.result()
            seen += 1
            if lic is None:
                errs += 1
            elif lic in OK_LICENCES:
                keep[pmcid] = {"version": v, "license_code": lic,
                               "title": d.get("title"), "doi": d.get("doi"),
                               "citation": d.get("citation"),
                               "retracted": d.get("is_retracted")}
            if seen % 5000 == 0:
                print(f"  meta {seen}/{len(ids)} kept={len(keep)} err={errs}", flush=True)
    json.dump(keep, open(out, "w"))
    print(f"  STAGE B done: {len(keep)} permissive of {len(ids)} (errors {errs})", flush=True)
    return keep


def _text(pmcid, v):
    try:
        r = sess().get(f"{S3}PMC{pmcid}.{v}/PMC{pmcid}.{v}.txt", timeout=90)
        if r.status_code == 200:
            return pmcid, r.text
    except Exception:
        pass
    return pmcid, None


def stage_c(keep, workers=48, out="raw/pmc_fulltext.jsonl.gz"):
    n, miss = 0, 0
    with gzip.open(out, "wt", encoding="utf-8") as fh, \
         ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_text, p, m["version"]): p for p, m in keep.items()}
        for f in as_completed(futs):
            pmcid, txt = f.result()
            if txt is None:
                miss += 1
                continue
            m = keep[pmcid]
            fh.write(json.dumps({"pmcid": "PMC" + pmcid, "license_code": m["license_code"],
                                 "title": m["title"], "doi": m["doi"],
                                 "citation": m["citation"], "text": txt},
                                ensure_ascii=False) + "\n")
            n += 1
            if n % 5000 == 0:
                print(f"  text {n} written, {miss} missing", flush=True)
    print(f"  STAGE C done: {n} articles, {miss} missing", flush=True)
    return n


if __name__ == "__main__":
    os.makedirs("raw", exist_ok=True)
    email = None
    if len(sys.argv) > 1 and "@" in sys.argv[1]:
        email = sys.argv[1]
    print("STAGE A: collecting PMCIDs", flush=True)
    ids = stage_a(email)
    print(f"  candidates: {len(ids)}", flush=True)
    print("STAGE B: licence filter", flush=True)
    keep = stage_b(ids)
    print("STAGE C: full text", flush=True)
    n = stage_c(keep)
    json.dump({"candidates": len(ids), "permissive": len(keep), "fetched": n},
              open("raw/pmc_stats.json", "w"), indent=2)
