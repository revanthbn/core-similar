"""
Step 7: Rank candidates into tiers and format the output payload.

After the LLM rerank refactor, tiers come directly from the model
(tier_1..tier_4). When `llm_tiers` is supplied to `rank_and_bucket`,
candidates are grouped by that label and sorted within each tier by
cosine similarity. When omitted (back-compat path) we fall back to the
original behaviour of bucketing by match-set bitmap.

The `to_dict()` output preserves the same JSON schema the UI consumes
(`evidence` keyed by criterion id, `cosine`, `criteria_matched/missing`,
`tier_label`), with a couple of additive fields for the LLM rationale.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pipeline.verify import CriterionMatch


TIER_ORDER = ("tier_1", "tier_2", "tier_3", "tier_4")
TIER_LABEL_PREFIX = {
    "tier_1": "Tier 1 — strong peers",
    "tier_2": "Tier 2 — plausible peers",
    "tier_3": "Tier 3 — adjacent",
    "tier_4": "Tier 4 — weakly related",
}


@dataclass
class TierCandidate:
    record_id: str
    name: str
    domain: str | None
    one_liner: str | None
    cosine: float
    matches: dict[int, dict]   # criterion_id -> {match, evidence}
    llm_tier: str | None = None
    tier_rationale: str = ""
    description_full: str | None = None

    def match_set(self) -> tuple[int, ...]:
        return tuple(sorted(cid for cid, m in self.matches.items() if m["match"]))

    def match_count(self) -> int:
        return sum(1 for m in self.matches.values() if m["match"])


@dataclass
class Tier:
    criteria_matched: list[int]
    criteria_missing: list[int]
    label: str
    candidates: list[TierCandidate]
    llm_tier: str | None = None
    summary_rationale: str = ""


@dataclass
class RankedOutput:
    seed: dict
    criteria: list[dict]
    tiers: list[Tier]
    candidate_count_total: int


def _legacy_tier_label(matched: list[int], all_criteria: list, missing: list[int]) -> str:
    if not missing:
        return "matches all criteria"
    missing_texts = [c.text for c in all_criteria if c.id in missing]
    missing_summary = "; ".join(missing_texts)[:160]
    return f"matches {len(matched)} of {len(all_criteria)} (missing: {missing_summary!r})"


def _build_candidate(
    rid: str,
    crit_ids: list[int],
    ms: list[CriterionMatch],
    candidate_scores: dict[str, float],
    candidate_meta: dict[str, dict],
    llm_tier: str | None,
    tier_rationale: str,
) -> TierCandidate:
    match_map: dict[int, dict] = {}
    for m in ms:
        match_map[m.criterion_id] = {
            "match":    m.match,
            "evidence": m.evidence,
        }
    # Backfill criteria the LLM omitted (default to NO_MATCH)
    for cid in crit_ids:
        if cid not in match_map:
            match_map[cid] = {"match": False, "evidence": ""}

    meta = candidate_meta.get(rid, {})
    return TierCandidate(
        record_id=rid,
        name=meta.get("name") or "",
        domain=meta.get("domain"),
        one_liner=meta.get("one_liner"),
        cosine=float(candidate_scores.get(rid, 0.0)),
        matches=match_map,
        llm_tier=llm_tier,
        tier_rationale=tier_rationale or "",
        description_full=meta.get("description_full") or meta.get("one_liner"),
    )


def rank_and_bucket(
    seed,
    criteria,
    candidate_ids: list[str],
    candidate_scores: dict[str, float],
    verifications: dict[str, list[CriterionMatch]],
    candidate_meta: dict[str, dict],
    *,
    llm_tiers: dict[str, str] | None = None,
    tier_rationales: dict[str, str] | None = None,
    per_tier_cap: int = 25,
) -> RankedOutput:
    """Group candidates into tiers.

    Preferred path: when `llm_tiers` is supplied, group by the model's
    tier_1..tier_4 label. Within a tier, sort by cosine desc.

    Fallback (no `llm_tiers`): group by match_set bitmap, the original
    behaviour. Kept so any callers that don't pass llm_tiers still work.
    """
    crit_ids = [c.id for c in criteria]
    tier_rationales = tier_rationales or {}

    candidates: list[TierCandidate] = []
    for rid in candidate_ids:
        ms = verifications.get(rid)
        if ms is None:
            continue
        candidates.append(_build_candidate(
            rid, crit_ids, ms,
            candidate_scores, candidate_meta,
            llm_tier=(llm_tiers or {}).get(rid),
            tier_rationale=tier_rationales.get(rid, ""),
        ))

    if llm_tiers:
        tier_buckets: dict[str, list[TierCandidate]] = defaultdict(list)
        for cand in candidates:
            key = cand.llm_tier if cand.llm_tier in TIER_ORDER else "tier_4"
            tier_buckets[key].append(cand)

        # Sort within tier by cosine desc; cap per tier
        for key, lst in tier_buckets.items():
            lst.sort(key=lambda c: -c.cosine)
            tier_buckets[key] = lst[:per_tier_cap]

        tiers: list[Tier] = []
        for key in TIER_ORDER:
            bucket = tier_buckets.get(key, [])
            if not bucket:
                continue
            # Use the rationale of the highest-cosine candidate in this tier
            # as a short summary line for the tier header.
            summary = bucket[0].tier_rationale if bucket else ""
            # Stable per-tier criteria_matched view: criteria matched by the
            # top (highest-cosine) candidate. This keeps the JSON schema
            # populated meaningfully without inventing aggregate semantics.
            top = bucket[0]
            matched = sorted(cid for cid, m in top.matches.items() if m["match"])
            missing = [cid for cid in crit_ids if cid not in matched]
            label_prefix = TIER_LABEL_PREFIX.get(key, key)
            label = label_prefix
            if summary:
                label = f"{label_prefix} — {summary[:160]}"

            tiers.append(Tier(
                criteria_matched=matched,
                criteria_missing=missing,
                label=label,
                candidates=bucket,
                llm_tier=key,
                summary_rationale=summary,
            ))

        return RankedOutput(
            seed={
                "name":         seed.name,
                "domain":       seed.domain,
                "record_id":    seed.record_id,
                "industries":   seed.industries[:6],
                "country":      seed.country,
            },
            criteria=[{"id": c.id, "text": c.text, "rationale": c.rationale} for c in criteria],
            tiers=tiers,
            candidate_count_total=sum(len(t.candidates) for t in tiers),
        )

    # ---- Fallback: legacy match-set bucketing ----
    tier_buckets_legacy: dict[tuple[int, ...], list[TierCandidate]] = defaultdict(list)
    for cand in candidates:
        tier_buckets_legacy[cand.match_set()].append(cand)
    for k, lst in tier_buckets_legacy.items():
        lst.sort(key=lambda c: -c.cosine)
        tier_buckets_legacy[k] = lst[:per_tier_cap]
    sorted_keys = sorted(tier_buckets_legacy.keys(), key=lambda k: (-len(k), k))
    tiers: list[Tier] = []
    for key in sorted_keys:
        matched = list(key)
        missing = [cid for cid in crit_ids if cid not in matched]
        tiers.append(Tier(
            criteria_matched=matched,
            criteria_missing=missing,
            label=_legacy_tier_label(matched, criteria, missing),
            candidates=tier_buckets_legacy[key],
        ))

    return RankedOutput(
        seed={
            "name":         seed.name,
            "domain":       seed.domain,
            "record_id":    seed.record_id,
            "industries":   seed.industries[:6],
            "country":      seed.country,
        },
        criteria=[{"id": c.id, "text": c.text, "rationale": c.rationale} for c in criteria],
        tiers=tiers,
        candidate_count_total=sum(len(t.candidates) for t in tiers),
    )


def to_dict(out: RankedOutput) -> dict:
    """Plain-dict form for JSON serialization. Schema-compatible with the UI:
    each candidate carries `evidence` keyed by criterion id. New fields
    (`llm_tier`, `tier_rationale`) are additive only."""
    return {
        "seed":       out.seed,
        "criteria":   out.criteria,
        "tiers": [
            {
                "tier_label":        t.label,
                "llm_tier":          t.llm_tier,
                "summary_rationale": t.summary_rationale,
                "criteria_matched":  t.criteria_matched,
                "criteria_missing":  t.criteria_missing,
                "candidates": [
                    {
                        "record_id":     c.record_id,
                        "name":          c.name,
                        "domain":        c.domain,
                        "one_liner":     c.one_liner,
                        "description_full": c.description_full,
                        "cosine":        round(c.cosine, 4),
                        "llm_tier":      c.llm_tier,
                        "tier_rationale": c.tier_rationale,
                        "evidence":      {str(cid): m for cid, m in c.matches.items()},
                    }
                    for c in t.candidates
                ],
            }
            for t in out.tiers
        ],
        "candidate_count_total": out.candidate_count_total,
    }
