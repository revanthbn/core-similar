"""
Step 1: Seed lookup against the unified corpus.

Loads the prebuilt index at data/cache/corpus_index.pkl (build via
`python -m scripts.build_corpus_index`) and fetches full rows on demand
via byte-offset seek. This avoids re-scanning the 5GB JSONL on every
query.

Public API:
    lookup_seed(name_or_domain, *, domain=None, name=None,
                founded_year=None, country=None) -> SeedRecord

The returned SeedRecord merges YC + CB rows when Tier 1 ER resolved
them to the same canonical entity, so downstream stages always get
the richest possible context for criterion extraction.
"""

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# Pipeline runs against the demo-trimmed corpus by default. The full
# 2.8M corpus and its 841MB index live at corpus_index.pkl and remain
# available for ad-hoc queries.
INDEX_PATH = REPO_ROOT / "data" / "cache" / "demo_index.pkl"
CORPUS_PATH = REPO_ROOT / "data" / "cache" / "demo_universe.jsonl"
TIER1_PATH = REPO_ROOT / "data" / "raw" / "yc_cb_tier1_resolution.jsonl"

from pipeline.text_normalize import extract_domain, normalize_name  # noqa: E402

_INDEX: dict[str, Any] = {}
_CORPUS_FH = None  # opened lazily


class SeedNotFoundError(LookupError):
    """Seed is not in the corpus."""


class SeedAmbiguousError(LookupError):
    """Multiple equally-good seed candidates; pass a hint to disambiguate."""

    def __init__(self, message: str, candidates: list[dict]):
        super().__init__(message)
        self.candidates = candidates


@dataclass
class SeedRecord:
    record_id: str
    sources: list[str]
    name: str
    name_norm: str
    domain: str | None
    website: str | None
    one_liner: str | None
    long_description: str | None
    industries: list[str]
    founded_year: int | None
    team_size: int | None
    num_employees_enum: str | None
    country: str | None
    locations: list[str]
    founders: list[str]
    yc_batch: str | None
    yc_record: dict | None = None
    cb_record: dict | None = None
    extras: dict = field(default_factory=dict)

    def combined_description(self) -> str:
        parts: list[str] = []
        if self.yc_record and self.yc_record.get("long_description"):
            parts.append(self.yc_record["long_description"])
        if self.yc_record and self.yc_record.get("one_liner"):
            parts.append(self.yc_record["one_liner"])
        if self.cb_record and self.cb_record.get("one_liner"):
            cb_ol = self.cb_record["one_liner"]
            if cb_ol not in parts:
                parts.append(cb_ol)
        return "\n".join(p for p in parts if p)


def _load_index() -> dict[str, Any]:
    global _INDEX
    if _INDEX:
        return _INDEX
    if not INDEX_PATH.exists():
        raise FileNotFoundError(
            f"Corpus index missing at {INDEX_PATH}. "
            f"Run: python -m scripts.build_corpus_index"
        )
    with INDEX_PATH.open("rb") as f:
        _INDEX = pickle.load(f)

    # Load tier1 unique-match map for YC<->CB merge
    yc_to_cb = {}
    cb_to_yc = {}
    if TIER1_PATH.exists():
        with TIER1_PATH.open() as f:
            for line in f:
                r = json.loads(line)
                if r.get("status") == "matched_unique" and r.get("matches"):
                    yc_id = r["yc_record_id"]
                    cb_id = r["matches"][0]["cb_record_id"]
                    yc_to_cb[yc_id] = cb_id
                    cb_to_yc[cb_id] = yc_id
    _INDEX["yc_to_cb_unique"] = yc_to_cb
    _INDEX["cb_to_yc_unique"] = cb_to_yc
    return _INDEX


def _corpus_fh():
    global _CORPUS_FH
    if _CORPUS_FH is None:
        _CORPUS_FH = CORPUS_PATH.open("rb")
    return _CORPUS_FH


def fetch_row(record_id: str) -> dict | None:
    """Random-access fetch of a single corpus row by record_id."""
    idx = _load_index()
    offset = idx["record_offsets"].get(record_id)
    if offset is None:
        return None
    fh = _corpus_fh()
    fh.seek(offset)
    line = fh.readline()
    return json.loads(line)


def fetch_rows(record_ids) -> dict[str, dict]:
    """Batch fetch multiple rows by record_id."""
    idx = _load_index()
    out = {}
    fh = _corpus_fh()
    # Sort by offset for sequential disk access
    sorted_ids = sorted(record_ids, key=lambda rid: idx["record_offsets"].get(rid, -1))
    for rid in sorted_ids:
        off = idx["record_offsets"].get(rid)
        if off is None:
            continue
        fh.seek(off)
        out[rid] = json.loads(fh.readline())
    return out


def _resolve_to_pair(primary_rid: str) -> list[str]:
    idx = _load_index()
    pair = [primary_rid]
    if primary_rid.startswith("yc:"):
        cb = idx["yc_to_cb_unique"].get(primary_rid)
        if cb:
            pair.append(cb)
    elif primary_rid.startswith("cb:"):
        yc = idx["cb_to_yc_unique"].get(primary_rid)
        if yc:
            pair.append(yc)
    return pair


def _merge_to_seed_record(rids: list[str]) -> SeedRecord:
    rows = fetch_rows(rids)
    yc = next((r for r in rows.values() if r.get("source") == "yc"), None)
    cb = next((r for r in rows.values() if r.get("source") == "crunchbase"), None)
    primary = cb or yc

    sources = []
    if yc: sources.append("yc")
    if cb: sources.append("crunchbase")

    def pick(field_name, default=None):
        v = None
        if cb and cb.get(field_name) is not None:
            v = cb[field_name]
        if (v is None or v == "" or v == []) and yc and yc.get(field_name) is not None:
            v = yc[field_name]
        return v if v not in (None, "", []) else default

    industries: list[str] = []
    if yc and yc.get("industries"):
        industries.extend(yc["industries"])
    if cb and cb.get("industries"):
        for c in cb["industries"]:
            if c not in industries:
                industries.append(c)

    locations: list[str] = []
    for src in (yc, cb):
        if src and src.get("locations"):
            for loc in src["locations"]:
                if loc and loc not in locations:
                    locations.append(loc)

    return SeedRecord(
        record_id=primary["record_id"],
        sources=sources,
        name=pick("name") or "",
        name_norm=pick("name_norm") or "",
        domain=pick("domain"),
        website=pick("website"),
        one_liner=pick("one_liner"),
        long_description=(yc.get("long_description") if yc else None),
        industries=industries,
        founded_year=pick("founded_year"),
        team_size=(yc.get("team_size") if yc else None),
        num_employees_enum=(cb.get("num_employees_enum") if cb else None),
        country=pick("country"),
        locations=locations,
        founders=(cb.get("founders") if cb else []) or [],
        yc_batch=(yc.get("yc_batch") if yc else None),
        yc_record=yc,
        cb_record=cb,
    )


def lookup_seed(
    name_or_domain: str | None = None,
    *,
    domain: str | None = None,
    name: str | None = None,
    founded_year: int | None = None,
    country: str | None = None,
) -> SeedRecord:
    """Resolve a seed to a canonical SeedRecord."""
    idx = _load_index()

    if name_or_domain and not (domain or name):
        # Direct record-id lookup (e.g. "cb:abc-..." or "yc:def-...") —
        # used by run_all_seeds.py to disambiguate seeds whose names collide.
        if (name_or_domain.startswith("cb:") or name_or_domain.startswith("yc:")) \
                and name_or_domain in idx.get("record_offsets", {}):
            return _merge_to_seed_record(_resolve_to_pair(name_or_domain))
        if "." in name_or_domain and " " not in name_or_domain:
            domain = name_or_domain
        else:
            name = name_or_domain

    norm_domain = extract_domain(domain) if domain else None
    norm_name = normalize_name(name) if name else None

    # Domain hit (preferred)
    if norm_domain:
        rids = idx["domain_to_records"].get(norm_domain, [])
        if rids:
            return _merge_to_seed_record(_resolve_to_pair(rids[0]))

    # Name hit
    if norm_name:
        rids = idx["name_to_records"].get(norm_name, [])
        if not rids:
            raise SeedNotFoundError(
                f"No corpus record with name_norm={norm_name!r} or domain={norm_domain!r}"
            )

        if founded_year is not None or country:
            filtered = []
            for rid in rids:
                row_meta = idx["compact"][rid]
                if founded_year is not None and row_meta.get("founded_year") is not None:
                    if abs(int(row_meta["founded_year"]) - int(founded_year)) > 2:
                        continue
                if country:
                    row_country = (row_meta.get("country") or "").lower()
                    if country.lower() not in row_country and row_country not in country.lower():
                        continue
                filtered.append(rid)
            if len(filtered) == 1:
                return _merge_to_seed_record(_resolve_to_pair(filtered[0]))
            if filtered:
                rids = filtered

        if len(rids) == 1:
            return _merge_to_seed_record(_resolve_to_pair(rids[0]))

        candidates = []
        for rid in rids[:10]:
            meta = idx["compact"][rid]
            candidates.append({
                "record_id":     rid,
                "name":          meta.get("name"),
                "domain":        meta.get("domain"),
                "founded_year":  meta.get("founded_year"),
                "country":       meta.get("country"),
                "industries":    meta.get("industries", [])[:4],
            })
        raise SeedAmbiguousError(
            f"Name {norm_name!r} matches {len(rids)} records; "
            f"pass domain= or founded_year= or country= to disambiguate",
            candidates,
        )

    raise SeedNotFoundError("No domain or name provided")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m pipeline.seed_lookup <name_or_domain>")
        sys.exit(1)
    try:
        s = lookup_seed(sys.argv[1])
    except SeedAmbiguousError as e:
        print(f"AMBIGUOUS: {e}")
        for c in e.candidates:
            print(f"  - {c['record_id']:50s} name={c['name']!r:30s} domain={c['domain']} year={c['founded_year']}")
        sys.exit(2)
    print(f"Resolved: {s.record_id} ({'+'.join(s.sources)})")
    print(f"  name: {s.name!r}")
    print(f"  domain: {s.domain}")
    print(f"  founded: {s.founded_year}")
    print(f"  industries: {s.industries[:6]}")
    print(f"  country: {s.country}")
    print(f"  team: yc.team_size={s.team_size} cb.hc={s.num_employees_enum}")
    print(f"  one_liner: {(s.one_liner or '')[:160]}")
    print(f"  combined_description first 200 chars:")
    cd = s.combined_description()
    print(f"    {cd[:200]}")
