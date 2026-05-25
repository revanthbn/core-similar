"""
Tier 1 entity resolution: YC -> Crunchbase via rules.

Rules:
  domain match           : yc.domain == cb.domain (both non-null)
  name + year match      : yc.name_norm == cb.name_norm
                            AND yc.founded_year, cb.founded_year non-null
                            AND |year_delta| <= NAME_YEAR_WINDOW (=2)

YC `former_names` are normalized and used as additional name keys; if the
current name and a former name resolve to different CB records, the row is
flagged tier1_ambiguous (we never silently pick).

Strict null-year handling: name-only matches against CB rows with no
founded_year fail closed. When that's the cause of a Tier-1 miss for a
YC row that *did* find name_norm candidates, the JSONL output records
failure_reason="cb_null_year" so Tier 2's description-cosine can later
quantify how many recall misses it would have recovered.

Run:
    python -m pipeline.candidate_generation
"""

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.text_normalize import normalize_name  # noqa: E402

CORPUS_PATH = REPO_ROOT / "data" / "raw" / "company_corpus.jsonl"
OUTPUT_PATH = REPO_ROOT / "data" / "raw" / "yc_cb_tier1_resolution.jsonl"
STATS_PATH = REPO_ROOT / "data" / "raw" / "tier1_stats.json"

NAME_YEAR_WINDOW = 2


# -------------------------------------------------------------------- index
def build_cb_indices(corpus_path):
    """Single streaming pass over the unified corpus. Returns:
      cb_by_id            : dict[cb_id -> minimal row dict]
      name_index          : dict[name_norm -> list[cb_id]]
      domain_index        : dict[domain   -> list[cb_id]]
      name_only_null_year : set[name_norm]  -- names that appear in CB but
                            ALWAYS with null founded_year (used to flag
                            cb_null_year miss reason)
    """
    cb_by_id = {}
    name_index = defaultdict(list)
    domain_index = defaultdict(list)
    name_year_seen = defaultdict(lambda: {"any_with_year": False, "any_without_year": False})

    t0 = time.time()
    n_cb = 0
    with corpus_path.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("source") != "crunchbase":
                continue
            n_cb += 1
            cb_id = r.get("record_id")
            if not cb_id:
                continue
            cb_by_id[cb_id] = {
                "record_id":   cb_id,
                "name":        r.get("name"),
                "name_norm":   r.get("name_norm"),
                "domain":      r.get("domain"),
                "founded_year": r.get("founded_year"),
                "permalink":   r.get("permalink"),
            }
            nn = r.get("name_norm")
            if nn:
                name_index[nn].append(cb_id)
                bucket = name_year_seen[nn]
                if r.get("founded_year") is not None:
                    bucket["any_with_year"] = True
                else:
                    bucket["any_without_year"] = True
            dom = r.get("domain")
            if dom:
                domain_index[dom].append(cb_id)

    name_only_null_year = {
        nn for nn, b in name_year_seen.items()
        if b["any_without_year"] and not b["any_with_year"]
    }

    print(f"  indexed {n_cb:,} CB rows in {time.time() - t0:.1f}s")
    print(f"    {len(name_index):,} unique name_norm")
    print(f"    {len(domain_index):,} unique domain")
    print(f"    {len(name_only_null_year):,} name_norms appear only with null founded_year\n")

    return cb_by_id, dict(name_index), dict(domain_index), name_only_null_year


# -------------------------------------------------------------------- resolve one
def _match_method(domain_hit, name_year_hit):
    if domain_hit and name_year_hit:
        return "both"
    if domain_hit:
        return "domain"
    return "name+year"


def resolve_yc_row(yc_row, cb_by_id, name_index, domain_index, name_only_null_year):
    """Returns a Tier-1 resolution dict for a single YC row."""
    yc_id = yc_row["record_id"]
    yc_name = yc_row.get("name")
    yc_name_norm = yc_row.get("name_norm") or ""
    yc_domain = yc_row.get("domain")
    yc_year = yc_row.get("founded_year")
    former_names = yc_row.get("former_names") or []

    name_keys = []  # list of (name_norm, source) tuples; source = "current" | "former"
    if yc_name_norm:
        name_keys.append((yc_name_norm, "current"))
    for fn in former_names:
        nn = normalize_name(fn)
        if nn and not any(nn == existing for existing, _ in name_keys):
            name_keys.append((nn, "former"))

    # Domain candidates
    domain_cands = set(domain_index.get(yc_domain, [])) if yc_domain else set()

    # Name+year candidates, partitioned by current vs former
    name_cands_by_source = defaultdict(set)  # source -> set[cb_id]
    name_only_null_year_seen = False
    for nn, src in name_keys:
        for cb_id in name_index.get(nn, []):
            cb_row = cb_by_id[cb_id]
            cb_year = cb_row.get("founded_year")
            if cb_year is None or yc_year is None:
                if cb_year is None:
                    name_only_null_year_seen = True
                continue
            if abs(int(cb_year) - int(yc_year)) <= NAME_YEAR_WINDOW:
                name_cands_by_source[src].add(cb_id)

    all_name_cands = set().union(*name_cands_by_source.values()) if name_cands_by_source else set()
    all_cands = domain_cands | all_name_cands

    # Build per-candidate match metadata
    matches = []
    for cb_id in sorted(all_cands):
        cb_row = cb_by_id[cb_id]
        domain_hit = cb_id in domain_cands
        name_year_hit = cb_id in all_name_cands
        cb_year = cb_row.get("founded_year")
        year_delta = (
            abs(int(cb_year) - int(yc_year))
            if cb_year is not None and yc_year is not None
            else None
        )
        matched_via_former_name = (
            cb_id in name_cands_by_source.get("former", set())
            and cb_id not in name_cands_by_source.get("current", set())
        )
        matches.append({
            "cb_record_id":            cb_id,
            "cb_name":                 cb_row.get("name"),
            "cb_domain":               cb_row.get("domain"),
            "cb_founded_year":         cb_year,
            "match_method":            _match_method(domain_hit, name_year_hit),
            "domain_match":            domain_hit,
            "name_match":              name_year_hit,
            "year_delta":              year_delta,
            "matched_via_former_name": matched_via_former_name,
        })

    # Determine status
    if not matches:
        # Did the YC name appear in CB at all (just with no usable year)?
        failure_reason = None
        if (any(nn in name_only_null_year for nn, _ in name_keys)
                or name_only_null_year_seen):
            failure_reason = "cb_null_year"
        elif yc_year is None and any(nn in name_index for nn, _ in name_keys):
            failure_reason = "yc_null_year"
        status = "absent_from_crunchbase"
        return {
            "yc_record_id":   yc_id,
            "yc_name":        yc_name,
            "status":         status,
            "n_matches":      0,
            "failure_reason": failure_reason,
            "matches":        [],
        }

    # Ambiguity check: current-name and former-name resolving to different CB
    current_set = name_cands_by_source.get("current", set()) | domain_cands
    former_set = name_cands_by_source.get("former", set())
    cross_name_split = (
        bool(current_set) and bool(former_set) and not (current_set & former_set)
    )

    if len(matches) == 1 and not cross_name_split:
        status = "matched_unique"
    else:
        status = "tier1_ambiguous"

    return {
        "yc_record_id":     yc_id,
        "yc_name":          yc_name,
        "status":           status,
        "n_matches":        len(matches),
        "cross_name_split": cross_name_split,
        "matches":          matches,
    }


# -------------------------------------------------------------------- run
def run(corpus_path=CORPUS_PATH, output_path=OUTPUT_PATH, stats_path=STATS_PATH):
    print(f"Reading corpus from {corpus_path}")
    cb_by_id, name_index, domain_index, name_only_null_year = build_cb_indices(corpus_path)

    status_counts = Counter()
    failure_reason_counts = Counter()
    n_match_dist = Counter()
    n_recovered_via_former = 0
    top_ambiguous = []
    method_counts = Counter()
    cross_name_split_count = 0

    t0 = time.time()
    n_yc = 0
    with corpus_path.open() as fin, output_path.open("w") as fout:
        for line in fin:
            r = json.loads(line)
            if r.get("source") != "yc":
                continue
            n_yc += 1
            res = resolve_yc_row(r, cb_by_id, name_index, domain_index, name_only_null_year)
            fout.write(json.dumps(res) + "\n")

            status_counts[res["status"]] += 1
            n_match_dist[min(res["n_matches"], 5)] += 1
            if res["status"] == "absent_from_crunchbase":
                failure_reason_counts[res.get("failure_reason") or "no_signal"] += 1
            if res.get("cross_name_split"):
                cross_name_split_count += 1
            for m in res["matches"]:
                method_counts[m["match_method"]] += 1
                if m.get("matched_via_former_name"):
                    n_recovered_via_former += 1
            if res["n_matches"] >= 5:
                top_ambiguous.append((res["yc_record_id"], res["yc_name"], res["n_matches"]))

    top_ambiguous.sort(key=lambda x: -x[2])
    top_ambiguous = top_ambiguous[:20]

    stats = {
        "yc_rows":               n_yc,
        "runtime_seconds":       round(time.time() - t0, 1),
        "status_counts":         dict(status_counts),
        "n_matches_distribution": {str(k): v for k, v in sorted(n_match_dist.items())},
        "match_method_counts":   dict(method_counts),
        "absent_failure_reasons": dict(failure_reason_counts),
        "cross_name_split_yc_rows": cross_name_split_count,
        "matches_recovered_via_former_name": n_recovered_via_former,
        "top_ambiguous_yc": [
            {"yc_record_id": rid, "yc_name": name, "n_matches": n}
            for rid, name, n in top_ambiguous
        ],
    }
    with stats_path.open("w") as f:
        json.dump(stats, f, indent=2)

    print("========= tier1_stats.json =========")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    run()
