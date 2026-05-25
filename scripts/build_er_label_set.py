"""
Generate a stratified, unlabeled template at data/eval/er_labels_TEMPLATE.jsonl
ready for hand-labeling against Crunchbase.

Strata (80 total):
  easy         : 20  proxy for well-known YC rows: yc_top_company=True
                     OR team_size >= 50 (only 7 YC rows in the 2019-2025
                     window are flagged top_company, so we widen via team
                     size to get a real "this should be easy for Tier 1"
                     bucket of ~364 candidates).
  random       : 25  uniform random over the YC 2019-2025 window
  rebrand      : 20  YC rows with non-empty former_names
  weak_domain  : 15  YC rows with no domain OR with a 2nd-tier TLD

For each sampled YC row, the template inlines Tier 1's top candidates so the
labeler can quickly verify or correct without re-running the resolver. The
labeler fills `ground_truth_cb_id` (or sets it to null for absent) and
optional `notes`, then saves as data/eval/er_labels.jsonl.

Deterministic via fixed RNG seed so the strata are reproducible.

Run:
    python -m scripts.build_er_label_set
"""

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CORPUS_PATH = REPO_ROOT / "data" / "raw" / "company_corpus.jsonl"
TIER1_PATH = REPO_ROOT / "data" / "raw" / "yc_cb_tier1_resolution.jsonl"
EVAL_DIR = REPO_ROOT / "data" / "eval"
TEMPLATE_PATH = EVAL_DIR / "er_labels_TEMPLATE.jsonl"

STRATA_SIZES = {"easy": 20, "random": 25, "rebrand": 20, "weak_domain": 15}
SEED = 42

# 2nd-tier TLDs that are commonly used but generic enough to be "weak"
WEAK_TLDS = {".co", ".io", ".ai", ".app", ".xyz", ".tech", ".tools"}


EASY_TEAM_SIZE = 50


def _classify_strata(yc):
    """Return the set of strata a YC row qualifies for. A row can be in
    multiple strata; sampling picks deterministically per stratum and
    de-dupes after."""
    out = set()
    ts = yc.get("team_size")
    if yc.get("yc_top_company") or (isinstance(ts, int) and ts >= EASY_TEAM_SIZE):
        out.add("easy")
    out.add("random")  # everything qualifies for random
    if yc.get("former_names"):
        out.add("rebrand")
    dom = yc.get("domain")
    if not dom:
        out.add("weak_domain")
    elif any(dom.endswith(tld) for tld in WEAK_TLDS):
        out.add("weak_domain")
    return out


def _build_label_row(yc, tier1_lookup):
    res = tier1_lookup.get(yc["record_id"], {})
    candidates = res.get("matches", [])[:5]
    return {
        # --- to fill in by labeler ---
        "ground_truth_cb_id": None,        # one of the cb_record_ids below, or null
        "difficulty":         None,        # filled by stratum below
        "notes":              "",

        # --- read-only context ---
        "yc_record_id":   yc["record_id"],
        "yc_name":        yc.get("name"),
        "yc_batch":       yc.get("yc_batch"),
        "yc_domain":      yc.get("domain"),
        "yc_one_liner":   yc.get("one_liner"),
        "yc_former_names": yc.get("former_names") or [],
        "yc_url":         yc.get("source_url"),

        "tier1_status":   res.get("status"),
        "tier1_n_matches": res.get("n_matches", 0),
        "tier1_candidates": [
            {
                "cb_record_id":     m["cb_record_id"],
                "cb_name":          m["cb_name"],
                "cb_domain":        m.get("cb_domain"),
                "cb_founded_year":  m.get("cb_founded_year"),
                "match_method":     m.get("match_method"),
                "matched_via_former_name": m.get("matched_via_former_name"),
            }
            for m in candidates
        ],
    }


def main():
    rng = random.Random(SEED)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    # Load Tier 1 resolutions keyed by yc_record_id
    tier1_lookup = {}
    with TIER1_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            tier1_lookup[r["yc_record_id"]] = r

    # Bucket YC rows into strata
    buckets = {s: [] for s in STRATA_SIZES}
    with CORPUS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("source") != "yc":
                continue
            for stratum in _classify_strata(r):
                if stratum in buckets:
                    buckets[stratum].append(r)

    print("Stratum candidate pool sizes:")
    for s, rows in buckets.items():
        print(f"  {s:14s} pool={len(rows):4d}  target={STRATA_SIZES[s]}")

    # Sample with cross-strata dedup. Order matters: pick rarer strata first
    # so they get priority claim on rows.
    order = ["rebrand", "weak_domain", "easy", "random"]
    chosen = {}  # record_id -> stratum (the assigned stratum, sticky)
    for stratum in order:
        pool = [r for r in buckets[stratum] if r["record_id"] not in chosen]
        rng.shuffle(pool)
        for r in pool[: STRATA_SIZES[stratum]]:
            chosen[r["record_id"]] = stratum

    # Assemble rows
    yc_by_id = {}
    with CORPUS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("source") == "yc" and r["record_id"] in chosen:
                yc_by_id[r["record_id"]] = r

    out_rows = []
    for rid, stratum in chosen.items():
        row = _build_label_row(yc_by_id[rid], tier1_lookup)
        row["difficulty"] = stratum
        out_rows.append(row)

    # Sort by stratum then yc_name for stable diffs
    stratum_order = {"easy": 0, "random": 1, "rebrand": 2, "weak_domain": 3}
    out_rows.sort(key=lambda r: (stratum_order[r["difficulty"]], r["yc_name"] or ""))

    with TEMPLATE_PATH.open("w") as f:
        for row in out_rows:
            f.write(json.dumps(row) + "\n")

    print(f"\nWrote {len(out_rows)} rows to {TEMPLATE_PATH}")
    print("Hand-label by: for each row, set `ground_truth_cb_id` to one of")
    print("the listed cb_record_id values, or null if no CB row truly matches.")
    print("Then save as data/eval/er_labels.jsonl")


if __name__ == "__main__":
    main()
