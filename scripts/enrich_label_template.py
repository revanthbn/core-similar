"""
Enrich data/eval/er_labels_TEMPLATE.jsonl with full context I need to label.

For each template row:
  - Inline the full YC corpus record (name, one_liner, long_description,
    industries, founded_year, locations, country, regions, team_size,
    yc_batch, former_names)
  - Inline the full CB corpus record for every cb_record_id present in
    tier1_candidates (one_liner, industries, founders, founded_year,
    locations, country, num_employees_enum, funding_total, linkedin)
  - For rows where tier1_status == "absent_from_crunchbase" OR where
    tier1 produced candidates but none look strong, ALSO fetch
    "near_matches": CB rows whose name_norm:
        - exactly equals yc.name_norm but failed year window
          (the cb_null_year cases), OR
        - contains yc.name_norm as a substring (or vice versa),
        - capped at 8 near-matches per template row

Output: data/eval/er_labels_enriched.jsonl
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CORPUS_PATH = REPO_ROOT / "data" / "raw" / "company_corpus.jsonl"
TEMPLATE_PATH = REPO_ROOT / "data" / "eval" / "er_labels_TEMPLATE.jsonl"
OUT_PATH = REPO_ROOT / "data" / "eval" / "er_labels_enriched.jsonl"

MAX_NEAR_MATCHES = 8


def _project_yc(r):
    return {
        "name":             r.get("name"),
        "name_norm":        r.get("name_norm"),
        "domain":           r.get("domain"),
        "website":          r.get("website"),
        "one_liner":        r.get("one_liner"),
        "long_description": r.get("long_description"),
        "industries":       r.get("industries"),
        "subindustry":      r.get("subindustry"),
        "tags":             r.get("tags"),
        "founded_year":     r.get("founded_year"),
        "team_size":        r.get("team_size"),
        "country":          r.get("country"),
        "regions":          r.get("regions"),
        "locations":        r.get("locations"),
        "yc_batch":         r.get("yc_batch"),
        "former_names":     r.get("former_names"),
        "yc_url":           r.get("source_url"),
    }


def _project_cb(r):
    return {
        "record_id":         r.get("record_id"),
        "name":              r.get("name"),
        "name_norm":         r.get("name_norm"),
        "domain":            r.get("domain"),
        "website":           r.get("website"),
        "one_liner":         r.get("one_liner"),
        "industries":        r.get("industries"),
        "founded_year":      r.get("founded_year"),
        "country":           r.get("country"),
        "locations":         r.get("locations"),
        "num_employees_enum": r.get("num_employees_enum"),
        "funding_total":     r.get("funding_total"),
        "founders":          r.get("founders"),
        "linkedin":          r.get("linkedin"),
        "operating_status":  r.get("operating_status"),
        "permalink":         r.get("permalink"),
    }


def main():
    # Load template
    template = []
    with TEMPLATE_PATH.open() as f:
        for line in f:
            template.append(json.loads(line))
    print(f"Loaded {len(template)} template rows")

    yc_ids_needed = {r["yc_record_id"] for r in template}
    cb_ids_needed = set()
    for r in template:
        for c in r.get("tier1_candidates", []):
            cb_ids_needed.add(c["cb_record_id"])

    yc_name_norms = {}      # yc_record_id -> name_norm (used for fuzzy lookup)
    for r in template:
        yc_name_norms[r["yc_record_id"]] = (r.get("yc_name") or "").lower().strip()

    # Build set of YC name_norms for fuzzy lookup across CB
    # We use the *normalized* name_norm as stored on the YC corpus row, which
    # is what we actually want to match. Fetch from the corpus shortly.
    yc_records = {}
    cb_records = {}

    # First pass: fetch YC and needed CB records
    print("Pass 1: fetch YC + named CB candidates...")
    with CORPUS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            rid = r.get("record_id")
            if r.get("source") == "yc" and rid in yc_ids_needed:
                yc_records[rid] = r
            elif r.get("source") == "crunchbase" and rid in cb_ids_needed:
                cb_records[rid] = r
            if len(yc_records) == len(yc_ids_needed) and len(cb_records) == len(cb_ids_needed):
                break

    print(f"  fetched {len(yc_records)}/{len(yc_ids_needed)} YC, "
          f"{len(cb_records)}/{len(cb_ids_needed)} CB candidate rows")

    # Build the set of yc name_norms to fuzzy-match against CB.
    # Restrict to template rows that need fuzzy lookup:
    #   - absent_from_crunchbase
    #   - tier1_ambiguous with many candidates (>5)
    fuzzy_targets = []   # list of (yc_record_id, yc_name_norm)
    for r in template:
        status = r["tier1_status"]
        if status == "absent_from_crunchbase":
            yc_r = yc_records.get(r["yc_record_id"])
            nn = (yc_r or {}).get("name_norm")
            if nn:
                fuzzy_targets.append((r["yc_record_id"], nn))
    fuzzy_targets_set = {t[1] for t in fuzzy_targets}

    print(f"\nPass 2: fuzzy-match {len(fuzzy_targets)} absent rows against full CB corpus...")
    # Single streaming pass over CB; for each row, check if its name_norm
    # is exact-equal or substring/superstring of any fuzzy target.
    fuzzy_hits = {nn: [] for nn in fuzzy_targets_set}
    n_scanned = 0
    with CORPUS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("source") != "crunchbase":
                continue
            n_scanned += 1
            cb_nn = r.get("name_norm") or ""
            if not cb_nn or len(cb_nn) < 3:
                continue
            for target in fuzzy_targets_set:
                if len(target) < 3:
                    continue
                bucket = fuzzy_hits[target]
                if len(bucket) >= MAX_NEAR_MATCHES:
                    continue
                if cb_nn == target:
                    bucket.append(r)
                elif target in cb_nn or cb_nn in target:
                    # Avoid trivial substring noise: require shared length to
                    # be a substantial fraction of the longer name.
                    short = min(len(cb_nn), len(target))
                    longn = max(len(cb_nn), len(target))
                    if short / longn >= 0.55:
                        bucket.append(r)
            if n_scanned % 500000 == 0:
                print(f"  scanned {n_scanned:,} CB rows...")
    print(f"  scan done; {sum(len(v) for v in fuzzy_hits.values())} fuzzy hits across "
          f"{sum(1 for v in fuzzy_hits.values() if v)} target names")

    # Stitch enriched output
    enriched_rows = []
    for r in template:
        yc_r = yc_records.get(r["yc_record_id"])
        enriched = {
            "yc_record_id":      r["yc_record_id"],
            "yc_name":           r["yc_name"],
            "difficulty":        r["difficulty"],
            "tier1_status":      r["tier1_status"],
            "tier1_n_matches":   r["tier1_n_matches"],
            "yc_full":           _project_yc(yc_r) if yc_r else None,
            "tier1_candidates_full": [],
            "near_matches_full": [],
        }
        for c in r.get("tier1_candidates", []):
            cb_r = cb_records.get(c["cb_record_id"])
            if cb_r:
                enriched["tier1_candidates_full"].append({
                    **_project_cb(cb_r),
                    "match_method":            c["match_method"],
                    "matched_via_former_name": c.get("matched_via_former_name"),
                })
        # Attach fuzzy near-matches for absent rows
        if r["tier1_status"] == "absent_from_crunchbase":
            yc_nn = (yc_r or {}).get("name_norm")
            for hit in fuzzy_hits.get(yc_nn, []):
                # Skip if it's already in the candidate set (shouldn't be, since
                # candidate set was empty for absent rows)
                enriched["near_matches_full"].append(_project_cb(hit))
        enriched_rows.append(enriched)

    with OUT_PATH.open("w") as f:
        for er in enriched_rows:
            f.write(json.dumps(er) + "\n")

    # Quick summary by difficulty
    from collections import Counter
    counts = Counter((r["difficulty"], r["tier1_status"]) for r in enriched_rows)
    print(f"\nWrote {OUT_PATH}")
    print("\nBy (difficulty, tier1_status):")
    for k, v in sorted(counts.items()):
        print(f"  {k[0]:14s} {k[1]:25s} {v:3d}")


if __name__ == "__main__":
    main()
