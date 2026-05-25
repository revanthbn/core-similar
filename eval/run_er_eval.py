"""
Score Tier 1 entity resolution against the hand-labeled eval set.

Inputs:
  data/eval/er_labels.jsonl           hand-labeled ground truth
  data/raw/yc_cb_tier1_resolution.jsonl  Tier 1 output

Outputs:
  Console: precision/recall/F1 table, overall + per stratum
  data/eval/er_metrics.json           machine-readable metrics
  data/eval/er_errors.jsonl           per-row FP/FN diagnostics

Reports two precision numbers separately:
  - Unique-match precision: of rows Tier 1 calls matched_unique, what
    fraction has its single match equal to the gold cb_record_id.
    The clean academic metric.
  - Top-candidate precision: of rows Tier 1 returns >=1 candidate for,
    after picking the strongest by match_method (both > domain >
    name+year, tiebroken by smaller year_delta and current over former
    name), what fraction equals the gold cb_record_id. The production
    metric: it's what you'd actually plumb into the similarity pool.

Run:
    python -m eval.run_er_eval
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

LABELS_PATH = REPO_ROOT / "data" / "eval" / "er_labels.jsonl"
TIER1_PATH = REPO_ROOT / "data" / "raw" / "yc_cb_tier1_resolution.jsonl"
METRICS_PATH = REPO_ROOT / "data" / "eval" / "er_metrics.json"
ERRORS_PATH = REPO_ROOT / "data" / "eval" / "er_errors.jsonl"

STRATA = ("easy", "random", "rebrand", "weak_domain")

METHOD_RANK = {"both": 3, "domain": 2, "name+year": 1}


def _pick_top_candidate(matches):
    """Return the highest-priority candidate per the top-candidate ranking."""
    if not matches:
        return None

    def key(m):
        return (
            -METHOD_RANK.get(m.get("match_method", ""), 0),
            1 if m.get("matched_via_former_name") else 0,
            m.get("year_delta") if m.get("year_delta") is not None else 99,
        )

    return sorted(matches, key=key)[0]


def _safe_div(num, den):
    return round(num / den, 4) if den else None


def evaluate():
    if not LABELS_PATH.exists():
        sys.exit(
            f"Labels file not found: {LABELS_PATH}\n"
            f"Hand-label the template at data/eval/er_labels_TEMPLATE.jsonl,\n"
            f"set ground_truth_cb_id on each row, save as er_labels.jsonl, "
            f"then re-run."
        )

    # Load Tier 1 keyed by yc_record_id
    tier1 = {}
    with TIER1_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            tier1[r["yc_record_id"]] = r

    # Load labels
    labels = []
    with LABELS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            labels.append(json.loads(line))

    # Per-row evaluation
    # Buckets we'll aggregate metrics over: "all" plus each stratum
    buckets = {"all": defaultdict(int)}
    for s in STRATA:
        buckets[s] = defaultdict(int)
    errors = []

    for lab in labels:
        yc_id = lab["yc_record_id"]
        gold = lab.get("ground_truth_cb_id")  # may be None for absent
        stratum = lab.get("difficulty") or "unknown"
        t1 = tier1.get(yc_id)
        if t1 is None:
            print(f"warning: no Tier 1 result for {yc_id}; skipping")
            continue
        status = t1["status"]
        matches = t1.get("matches", [])
        match_ids = [m["cb_record_id"] for m in matches]
        top = _pick_top_candidate(matches)
        top_id = top["cb_record_id"] if top else None

        is_present = gold is not None
        gold_in_candidates = (gold is not None) and (gold in match_ids)
        unique_correct = (
            status == "matched_unique"
            and is_present
            and match_ids == [gold]
        )
        top_correct = (
            is_present
            and top_id is not None
            and top_id == gold
        )

        for b in ("all", stratum):
            bucket = buckets[b]
            bucket["labels_total"] += 1
            if is_present:
                bucket["present"] += 1
            else:
                bucket["absent"] += 1

            # Unique-match
            if status == "matched_unique":
                bucket["tier1_unique_predictions"] += 1
                if unique_correct:
                    bucket["tier1_unique_correct"] += 1

            # Top-candidate (denominator: tier1 returned any matches)
            if matches:
                bucket["tier1_any_candidate"] += 1
                if top_correct:
                    bucket["tier1_top_correct"] += 1

            # Candidate recall
            if is_present:
                if gold_in_candidates:
                    bucket["recall_hit"] += 1

            # Absent precision
            if status == "absent_from_crunchbase":
                bucket["tier1_absent_predictions"] += 1
                if not is_present:
                    bucket["tier1_absent_correct"] += 1

        # Error logging
        is_error = (
            (is_present and not gold_in_candidates)         # missed
            or (status == "matched_unique" and not unique_correct)  # wrong unique
            or (matches and not top_correct)                # wrong top pick
            or (status == "absent_from_crunchbase" and is_present)  # missed entirely
        )
        if is_error:
            errors.append({
                "yc_record_id":           yc_id,
                "yc_name":                lab.get("yc_name"),
                "difficulty":             stratum,
                "gold_cb_id":             gold,
                "tier1_status":           status,
                "tier1_n_matches":        len(matches),
                "tier1_match_ids":        match_ids,
                "tier1_top_pick":         top_id,
                "tier1_failure_reason":   t1.get("failure_reason"),
                "gold_in_candidates":     gold_in_candidates,
                "unique_correct":         unique_correct,
                "top_correct":            top_correct,
                "notes":                  lab.get("notes", ""),
            })

    # Compute summary metrics per bucket
    def summarize(b):
        unique_precision = _safe_div(b["tier1_unique_correct"], b["tier1_unique_predictions"])
        top_precision = _safe_div(b["tier1_top_correct"], b["tier1_any_candidate"])
        recall = _safe_div(b["recall_hit"], b["present"])
        absent_precision = _safe_div(b["tier1_absent_correct"], b["tier1_absent_predictions"])
        f1 = None
        if unique_precision is not None and recall is not None and (unique_precision + recall) > 0:
            f1 = round(2 * unique_precision * recall / (unique_precision + recall), 4)
        return {
            "n_labels":           b["labels_total"],
            "n_present":          b["present"],
            "n_absent":           b["absent"],
            "unique_match_precision":    unique_precision,
            "top_candidate_precision":   top_precision,
            "candidate_recall":          recall,
            "absent_precision":          absent_precision,
            "f1_unique_match_recall":    f1,
            "counts": {
                "tier1_unique_predictions": b["tier1_unique_predictions"],
                "tier1_unique_correct":     b["tier1_unique_correct"],
                "tier1_any_candidate":      b["tier1_any_candidate"],
                "tier1_top_correct":        b["tier1_top_correct"],
                "tier1_absent_predictions": b["tier1_absent_predictions"],
                "tier1_absent_correct":     b["tier1_absent_correct"],
                "recall_hit":               b["recall_hit"],
            },
        }

    metrics = {"overall": summarize(buckets["all"])}
    for s in STRATA:
        metrics[s] = summarize(buckets[s])

    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_PATH.open("w") as f:
        json.dump(metrics, f, indent=2)
    with ERRORS_PATH.open("w") as f:
        for e in errors:
            f.write(json.dumps(e) + "\n")

    # Console table
    def fmt(v):
        return "  --  " if v is None else f"{v * 100:5.1f}%"

    cols = ["n", "n_pres", "uniq_P", "top_P", "recall", "absent_P", "F1"]
    print()
    print(f"{'stratum':<14} " + " ".join(f"{c:>8s}" for c in cols))
    print("-" * 80)
    for name in ("overall",) + STRATA:
        m = metrics[name]
        row = [
            f"{m['n_labels']:>8d}",
            f"{m['n_present']:>8d}",
            f"{fmt(m['unique_match_precision']):>8s}",
            f"{fmt(m['top_candidate_precision']):>8s}",
            f"{fmt(m['candidate_recall']):>8s}",
            f"{fmt(m['absent_precision']):>8s}",
            f"{fmt(m['f1_unique_match_recall']):>8s}",
        ]
        print(f"{name:<14} " + " ".join(row))

    print(f"\nWrote {METRICS_PATH}")
    print(f"Wrote {ERRORS_PATH}  ({len(errors)} error rows)")


if __name__ == "__main__":
    evaluate()
