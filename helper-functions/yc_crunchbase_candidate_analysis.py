"""
yc_crunchbase_candidate_analysis.py
-----------------------------------
For each YC company in the seed, find candidate Crunchbase records via
normalized-name and domain matching. Report the distribution of match counts
and surface high-ambiguity cases (these become your collision adversarial set).

Run AFTER fetch_yc_seed.py, AFTER placing your Crunchbase CSV at the path
configured below.

Outputs:
    data/raw/yc_crunchbase_candidates.jsonl  — per-YC-company candidate list
    Stdout: summary stats + top-N ambiguous cases
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

# ---- Config ----
YC_SEED_PATH = Path("data/raw/yc_seed.jsonl")
CRUNCHBASE_CSV_PATH = Path("data/raw/crunchbase_2024_08.csv")  # update to your path
OUTPUT_DIR = Path("data/raw")

# Crunchbase columns we actually need for ER candidate analysis
CB_USECOLS = [
    "id", "name", "short_description", "website", "linkedin", "twitter",
    "facebook", "founded_on", "categories", "founders", "locations",
    "permalink", "url",
]

CORPORATE_SUFFIXES = [
    ", inc.", ", inc", " inc.", " inc",
    ", llc.", ", llc", " llc.", " llc",
    ", ltd.", ", ltd", " ltd.", " ltd",
    " co.", " co", " corp.", " corp", " corporation",
    " s.p.a.", " spa", " s.r.l.", " srl",
    " gmbh", " ag", " ab", " bv", " sa", " sas",
    " pte. ltd.", " pte ltd",
]


def normalize_name(s):
    """Lowercase, strip common corporate suffixes, collapse whitespace/punct."""
    if not s or pd.isna(s):
        return ""
    s = str(s).lower().strip()
    for suffix in CORPORATE_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_domain(url):
    """Pull just the domain from a URL."""
    if not url or pd.isna(url):
        return None
    m = re.search(r"https?://(?:www\.)?([^/]+)", str(url))
    return m.group(1).lower() if m else None


def main():
    # ---- Load YC seed ----
    print("Loading YC seed...")
    with YC_SEED_PATH.open() as f:
        yc_companies = [json.loads(line) for line in f]
    print(f"  {len(yc_companies)} YC companies\n")

    # ---- Load Crunchbase (only the columns we need to save memory) ----
    print(f"Loading Crunchbase CSV: {CRUNCHBASE_CSV_PATH}")
    print("  (may take 30-90s for 2.8M rows)")
    cb = pd.read_csv(
        CRUNCHBASE_CSV_PATH,
        usecols=CB_USECOLS,
        dtype=str,
        low_memory=False,
        on_bad_lines="skip",
    )
    print(f"  {len(cb):,} Crunchbase records loaded\n")

    # ---- Normalize names + extract domains ----
    print("Normalizing names and domains...")
    cb["name_norm"] = cb["name"].apply(normalize_name)
    cb["domain"] = cb["website"].apply(extract_domain)

    # ---- Build lookup indices ----
    print("Building lookup indices...")
    name_to_idx = defaultdict(list)
    domain_to_idx = defaultdict(list)
    for idx, (name_norm, domain) in enumerate(zip(cb["name_norm"], cb["domain"])):
        if name_norm:
            name_to_idx[name_norm].append(idx)
        if domain:
            domain_to_idx[domain].append(idx)
    print(f"  {len(name_to_idx):,} unique normalized names")
    print(f"  {len(domain_to_idx):,} unique domains\n")

    # ---- Convert CB to list-of-dicts for fast indexed access ----
    cb_records = cb.to_dict(orient="records")

    # ---- For each YC company, find candidates ----
    print("Finding candidates for each YC company...")
    bucket_counts = Counter()
    yc_with_candidates = {}

    for yc in yc_companies:
        yc_name_norm = normalize_name(yc.get("name"))
        yc_domain = extract_domain(yc.get("website"))

        cands_by_name = set(name_to_idx.get(yc_name_norm, []))
        cands_by_domain = set(domain_to_idx.get(yc_domain, [])) if yc_domain else set()

        all_cands = cands_by_name | cands_by_domain
        n = len(all_cands)
        bucket = (
            "0" if n == 0 else
            "1" if n == 1 else
            "2-5" if n <= 5 else
            "6-20" if n <= 20 else
            "21+"
        )
        bucket_counts[bucket] += 1

        if all_cands:
            yc_with_candidates[yc["slug"]] = {
                "yc_name": yc["name"],
                "yc_website": yc.get("website"),
                "yc_batch": yc.get("batch"),
                "yc_one_liner": yc.get("one_liner"),
                "candidates": [
                    {
                        "cb_id": cb_records[idx].get("id"),
                        "cb_name": cb_records[idx].get("name"),
                        "cb_website": cb_records[idx].get("website"),
                        "cb_permalink": cb_records[idx].get("permalink"),
                        "cb_short_description": cb_records[idx].get("short_description"),
                        "cb_founded_on": cb_records[idx].get("founded_on"),
                        "match_via": (
                            "both" if idx in cands_by_name and idx in cands_by_domain
                            else "name" if idx in cands_by_name
                            else "domain"
                        ),
                    }
                    for idx in all_cands
                ],
            }

    # ---- Save and report ----
    out_path = OUTPUT_DIR / "yc_crunchbase_candidates.jsonl"
    with out_path.open("w") as f:
        for slug, data in yc_with_candidates.items():
            f.write(json.dumps({"slug": slug, **data}) + "\n")

    print(f"\nSaved {out_path}")

    print("\n========= Candidate distribution =========")
    for bucket in ["0", "1", "2-5", "6-20", "21+"]:
        n = bucket_counts[bucket]
        pct = 100 * n / len(yc_companies)
        print(f"  {bucket:5s} candidates : {n:5d}  ({pct:5.1f}%)")

    print("\n========= Top 20 highest-ambiguity YC companies =========")
    print("(these are your collision adversarial set)")
    sorted_by_ambig = sorted(
        yc_with_candidates.items(),
        key=lambda kv: len(kv[1]["candidates"]),
        reverse=True,
    )
    for slug, data in sorted_by_ambig[:20]:
        print(f"  {data['yc_name'][:35]:35s}  {len(data['candidates']):3d} candidates  ({data.get('yc_batch')})")

    print("\n========= Sample 'absence' cases (YC companies with 0 Crunchbase candidates) =========")
    absent = [yc for yc in yc_companies if normalize_name(yc.get("name")) not in name_to_idx
              and (not extract_domain(yc.get("website")) or extract_domain(yc.get("website")) not in domain_to_idx)]
    print(f"  Total: {len(absent)}")
    for yc in absent[:10]:
        print(f"    {yc['name'][:35]:35s}  ({yc.get('batch')})")


if __name__ == "__main__":
    main()
