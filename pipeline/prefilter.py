"""
Step 4: Categorical pre-filter.

Given a resolved seed, returns the candidate record_ids that pass cheap
categorical checks. This is the corpus the embedding step (step 5)
operates on, NOT the full demo universe.

Filters:
  - Industry overlap: at least one shared tag between seed.industries and
    candidate.industries (uses the demo_index's industry inverted index)
  - operating_status == "active" (CB) or source == "yc"
  - has_description (so embedding has something to encode)
  - Optional headcount band (off by default; the user can enable per query)

Returns ~3K-30K candidates per typical seed.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from pipeline.seed_lookup import _load_index


# Tags too broad to drive candidate generation. If the seed has any of
# these PLUS at least one more specific tag, we drop the broad ones. If
# all the seed's tags are in this set, we keep them (avoids empty result).
BROAD_TAGS = {
    "Software", "Enterprise Software", "B2B", "Apps", "Mobile Apps",
    "Web Apps", "Internet", "Information Technology", "SaaS",
    "Consumer", "Consumer Goods",
}


# Headcount enum bucket order (CB's num_employees_enum)
HC_BUCKETS = [
    "c_00001_00010",
    "c_00011_00050",
    "c_00051_00100",
    "c_00101_00250",
    "c_00251_00500",
    "c_00501_01000",
    "c_01001_05000",
    "c_05001_10000",
    "c_10001_max",
]
HC_BUCKET_IDX = {b: i for i, b in enumerate(HC_BUCKETS)}


@dataclass
class PrefilterResult:
    candidate_ids: list[str]
    seed_industries_used: list[str]
    n_industry_hits: int
    n_after_active: int
    n_after_description: int
    n_after_headcount: int
    n_final: int


def _team_size_to_bucket_idx(team_size: int | None) -> int | None:
    """Map YC team_size (raw int) onto the CB enum bucket index."""
    if team_size is None:
        return None
    if team_size < 10:  return 0
    if team_size <= 50: return 1
    if team_size <= 100: return 2
    if team_size <= 250: return 3
    if team_size <= 500: return 4
    if team_size <= 1000: return 5
    if team_size <= 5000: return 6
    if team_size <= 10000: return 7
    return 8


def _seed_hc_idx(seed) -> int | None:
    if seed.num_employees_enum:
        return HC_BUCKET_IDX.get(seed.num_employees_enum)
    return _team_size_to_bucket_idx(seed.team_size)


def prefilter(
    seed,
    extra_industries: Iterable[str] | None = None,
    headcount_band: int | None = None,
    drop_self: bool = True,
) -> PrefilterResult:
    """Return candidates that pass cheap categorical checks.

    Args:
      seed: a SeedRecord
      extra_industries: optional industry tags to widen the search (e.g.
        Crustdata taxonomy that doesn't appear in seed.industries)
      headcount_band: if not None, restrict to candidates within ±N buckets
        of the seed's headcount bucket. None = no headcount filter.
      drop_self: drop the seed's own record_id from candidates.
    """
    idx = _load_index()

    # Build industry set: seed industries + any extras
    seed_inds = set(seed.industries or [])
    if extra_industries:
        seed_inds.update(extra_industries)
    seed_inds = {ind for ind in seed_inds if ind}

    # Drop broad tags IF seed has more specific tags too; otherwise keep
    # them (so seeds with only broad tags still produce candidates).
    specific = seed_inds - BROAD_TAGS
    if specific:
        seed_inds = specific

    # Union of records whose industry overlaps the seed
    industry_hits = set()
    for ind in seed_inds:
        industry_hits.update(idx["industry_to_records"].get(ind, []))
    n_industry = len(industry_hits)

    # Active + has_description filters; YC always passes
    after_active = set()
    after_desc = set()
    compact = idx["compact"]
    for rid in industry_hits:
        meta = compact.get(rid, {})
        src = meta.get("source")
        if src == "yc":
            after_active.add(rid)
        elif src == "crunchbase":
            if meta.get("operating_status") == "active":
                after_active.add(rid)
    for rid in after_active:
        meta = compact[rid]
        if meta.get("has_description"):
            after_desc.add(rid)

    # Headcount band
    if headcount_band is not None:
        seed_hc = _seed_hc_idx(seed)
        if seed_hc is None:
            after_hc = after_desc  # can't filter; pass-through
        else:
            after_hc = set()
            for rid in after_desc:
                meta = compact[rid]
                # YC: use team_size; CB: use num_employees_enum
                if meta.get("source") == "yc":
                    cand_hc = _team_size_to_bucket_idx(meta.get("team_size"))
                else:
                    cand_hc = HC_BUCKET_IDX.get(meta.get("num_employees_enum")) if meta.get("num_employees_enum") else None
                if cand_hc is None:
                    # Keep — missing data shouldn't drop
                    after_hc.add(rid)
                elif abs(cand_hc - seed_hc) <= headcount_band:
                    after_hc.add(rid)
    else:
        after_hc = after_desc

    if drop_self and seed.record_id in after_hc:
        after_hc.discard(seed.record_id)

    return PrefilterResult(
        candidate_ids=list(after_hc),
        seed_industries_used=sorted(seed_inds),
        n_industry_hits=n_industry,
        n_after_active=len(after_active),
        n_after_description=len(after_desc),
        n_after_headcount=len(after_hc),
        n_final=len(after_hc),
    )


if __name__ == "__main__":
    import sys
    from pipeline.seed_lookup import lookup_seed
    if len(sys.argv) < 2:
        print("usage: python -m pipeline.prefilter <name_or_domain> [--hcband N]")
        sys.exit(1)
    hcband = None
    args = sys.argv[1:]
    if "--hcband" in args:
        i = args.index("--hcband")
        hcband = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    seed = lookup_seed(args[0])
    print(f"Seed: {seed.name} ({seed.record_id}); industries={seed.industries[:5]}; hc_idx={_seed_hc_idx(seed)}")
    r = prefilter(seed, headcount_band=hcband)
    print(f"  industries used:    {len(r.seed_industries_used)}")
    print(f"  industry hits:      {r.n_industry_hits:,}")
    print(f"  after active+yc:    {r.n_after_active:,}")
    print(f"  after has_desc:     {r.n_after_description:,}")
    print(f"  after headcount:    {r.n_after_headcount:,}")
    print(f"  final candidates:   {r.n_final:,}")
