"""
build_company_corpus.py
-----------------------
Curate the unified company corpus consumed by the rest of CoreSimilar.

Loads YC OSS (2019-2025 batches) + the local 2.8M Crunchbase snapshot,
maps both to a single unified schema, writes one JSONL row per company
to data/raw/company_corpus.jsonl, and emits a stats file at
data/raw/corpus_stats.json for the README/verification.

Does NOT perform entity resolution. YC and CB rows for the same company
will both appear as separate rows; the ER module joins them later.

Run:
    python scripts/build_company_corpus.py
"""

import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.text_normalize import extract_domain, normalize_name  # noqa: E402

# ---- Config ----
YC_ALL_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_BATCHES = {
    f"{season} {year}"
    for year in range(2019, 2026)
    for season in ("Winter", "Summer")
}
CB_DIR = REPO_ROOT / "crunchbase_2.8m"
OUTPUT_DIR = REPO_ROOT / "data" / "raw"
CORPUS_PATH = OUTPUT_DIR / "company_corpus.jsonl"
STATS_PATH = OUTPUT_DIR / "corpus_stats.json"

CB_USECOLS = [
    "id", "name", "short_description", "website", "linkedin", "twitter",
    "facebook", "founded_on", "categories", "founders", "locations",
    "num_employees_enum", "funding_total", "last_funding_type",
    "last_funding_at", "operating_status", "permalink", "url",
]

CB_CHUNKSIZE = 50_000

REQUIRED_KEYS = ("record_id", "source", "name", "name_norm", "domain", "raw")

# Fields we report a null-rate on (everything below is informational, not pass/fail)
STATS_FIELDS = (
    "name", "name_norm", "domain", "website", "one_liner", "long_description",
    "industries", "founded_on", "founded_year", "team_size",
    "num_employees_enum", "locations", "country", "funding_total",
    "last_funding_type", "founders", "linkedin",
)


def _clean_str(x):
    """Return stripped str or None for empty / NaN-ish values."""
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _split_csv_list(x):
    """Comma-separated text -> list[str]. Empty input -> []."""
    s = _clean_str(x)
    if not s:
        return []
    return [part.strip() for part in s.split(",") if part.strip()]


def _parse_founded_on(x):
    """Return (iso_date, year) or (None, None)."""
    s = _clean_str(x)
    if not s:
        return None, None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        return None, None
    return dt.strftime("%Y-%m-%d"), int(dt.year)


_NUM_RE = re.compile(r"[^0-9.\-]")


def _parse_funding_total(x):
    """Strip currency formatting, return float or None."""
    s = _clean_str(x)
    if not s:
        return None
    cleaned = _NUM_RE.sub("", s)
    if not cleaned or cleaned in (".", "-"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _derive_country_from_cb_locations(locations):
    """CB locations is roughly 'City, State, Country, Region, ...'.
    No structured field for country — heuristic: take the third entry if there
    are 3+ comma-separated parts; otherwise the last. Cheap, lossy, good enough
    for the curation layer (the ER module shouldn't depend on this).
    """
    if not locations:
        return None
    if len(locations) >= 3:
        return locations[2]
    return locations[-1]


def _derive_country_from_yc(yc_record):
    """YC has 'all_locations' (string) and 'country' isn't a direct field;
    'regions' is a list of broad regions. Best signal: parse last token of
    all_locations (e.g. 'San Francisco, CA, USA' -> 'USA')."""
    loc = _clean_str(yc_record.get("all_locations"))
    if loc:
        last = loc.split(",")[-1].strip()
        if last:
            return last
    regions = yc_record.get("regions") or []
    if regions:
        return regions[0]
    return None


# -------------------------------------------------------------------- YC
def map_yc_row(yc):
    name = _clean_str(yc.get("name"))
    website = _clean_str(yc.get("website"))
    slug = _clean_str(yc.get("slug")) or ""

    # founded_year from launched_at (unix timestamp) if present
    founded_on, founded_year = None, None
    launched_at = yc.get("launched_at")
    if isinstance(launched_at, (int, float)) and launched_at > 0:
        try:
            dt = pd.to_datetime(int(launched_at), unit="s")
            founded_on = dt.strftime("%Y-%m-%d")
            founded_year = int(dt.year)
        except (ValueError, OverflowError):
            pass

    industries = yc.get("industries") or []
    if not isinstance(industries, list):
        industries = []
    tags = yc.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    regions = yc.get("regions") or []
    if not isinstance(regions, list):
        regions = []
    former_names = yc.get("former_names") or []
    if not isinstance(former_names, list):
        former_names = []

    locations_raw = _clean_str(yc.get("all_locations"))
    locations = [locations_raw] if locations_raw else []

    return {
        "record_id": f"yc:{slug}",
        "source": "yc",
        "source_id": slug,
        "source_url": _clean_str(yc.get("url")),
        "name": name,
        "name_norm": normalize_name(name),
        "former_names": former_names,
        "website": website,
        "domain": extract_domain(website),
        "linkedin": None,
        "twitter": None,
        "facebook": None,
        "one_liner": _clean_str(yc.get("one_liner")),
        "long_description": _clean_str(yc.get("long_description")),
        "industries": industries,
        "subindustry": _clean_str(yc.get("subindustry")),
        "tags": tags,
        "founded_on": founded_on,
        "founded_year": founded_year,
        "team_size": yc.get("team_size") if isinstance(yc.get("team_size"), int) else None,
        "num_employees_enum": None,
        "locations": locations,
        "regions": regions,
        "country": _derive_country_from_yc(yc),
        "funding_total": None,
        "last_funding_type": None,
        "last_funding_at": None,
        "founders": [],
        "yc_batch": _clean_str(yc.get("batch")),
        "yc_status": _clean_str(yc.get("status")),
        "yc_top_company": bool(yc.get("top_company")) if yc.get("top_company") is not None else None,
        "operating_status": None,
        "permalink": None,
        "raw": yc,
    }


# -------------------------------------------------------------------- CB
def map_cb_row(row):
    """row: dict (one Crunchbase CSV row as str values)."""
    name = _clean_str(row.get("name"))
    website = _clean_str(row.get("website"))
    cb_id = _clean_str(row.get("id"))
    founded_on, founded_year = _parse_founded_on(row.get("founded_on"))
    locations = _split_csv_list(row.get("locations"))

    return {
        "record_id": f"cb:{cb_id}" if cb_id else f"cb:_missing_{id(row)}",
        "source": "crunchbase",
        "source_id": cb_id,
        "source_url": _clean_str(row.get("url")),
        "name": name,
        "name_norm": normalize_name(name),
        "former_names": [],
        "website": website,
        "domain": extract_domain(website),
        "linkedin": _clean_str(row.get("linkedin")),
        "twitter": _clean_str(row.get("twitter")),
        "facebook": _clean_str(row.get("facebook")),
        "one_liner": _clean_str(row.get("short_description")),
        "long_description": None,
        "industries": _split_csv_list(row.get("categories")),
        "subindustry": None,
        "tags": [],
        "founded_on": founded_on,
        "founded_year": founded_year,
        "team_size": None,
        "num_employees_enum": _clean_str(row.get("num_employees_enum")),
        "locations": locations,
        "regions": [],
        "country": _derive_country_from_cb_locations(locations),
        "funding_total": _parse_funding_total(row.get("funding_total")),
        "last_funding_type": _clean_str(row.get("last_funding_type")),
        "last_funding_at": _clean_str(row.get("last_funding_at")),
        "founders": _split_csv_list(row.get("founders")),
        "yc_batch": None,
        "yc_status": None,
        "yc_top_company": None,
        "operating_status": _clean_str(row.get("operating_status")),
        "permalink": _clean_str(row.get("permalink")),
        "raw": {k: row.get(k) for k in CB_USECOLS},
    }


# -------------------------------------------------------------------- stats
class StatsAccumulator:
    def __init__(self):
        self.total = 0
        self.by_source = Counter()
        self.null_counts = {f: 0 for f in STATS_FIELDS}
        self.name_norm_counts = Counter()
        self.first_record_keys = None
        self.schema_violations = 0

    def update(self, row):
        self.total += 1
        self.by_source[row["source"]] += 1

        if self.first_record_keys is None:
            self.first_record_keys = set(row.keys())
        if not all(k in row for k in REQUIRED_KEYS):
            self.schema_violations += 1

        for f in STATS_FIELDS:
            v = row.get(f)
            if v is None or (isinstance(v, list) and not v):
                self.null_counts[f] += 1

        nn = row.get("name_norm")
        if nn:
            self.name_norm_counts[nn] += 1

    def to_json(self, top_n_collisions=20):
        null_rates = {
            f: round(self.null_counts[f] / self.total, 4) if self.total else None
            for f in STATS_FIELDS
        }
        # Collisions: name_norm values appearing >=5 times, top N by count.
        top_collisions = [
            {"name_norm": name, "count": count}
            for name, count in self.name_norm_counts.most_common()
            if count >= 5
        ][:top_n_collisions]
        return {
            "total_rows": self.total,
            "by_source": dict(self.by_source),
            "schema_violations": self.schema_violations,
            "null_rates": null_rates,
            "unique_name_norm": len(self.name_norm_counts),
            "top_name_norm_collisions": top_collisions,
        }


# -------------------------------------------------------------------- main
def fetch_yc_records():
    print(f"Fetching YC OSS: {YC_ALL_URL}")
    resp = requests.get(YC_ALL_URL, timeout=120)
    resp.raise_for_status()
    all_companies = resp.json()
    in_window = [c for c in all_companies if c.get("batch") in YC_BATCHES]
    print(f"  YC total: {len(all_companies)};  in 2019-2025 window: {len(in_window)}")
    return in_window


def iter_cb_rows():
    """Yield CB CSV rows as dicts, streaming across all shards in chunks."""
    shard_paths = sorted(CB_DIR.glob("crunchbase_*.csv"))
    if not shard_paths:
        raise FileNotFoundError(f"No Crunchbase shards found in {CB_DIR}")
    print(f"  Found {len(shard_paths)} Crunchbase shards")
    for sp in shard_paths:
        t0 = time.time()
        shard_count = 0
        for chunk in pd.read_csv(
            sp,
            usecols=CB_USECOLS,
            dtype=str,
            chunksize=CB_CHUNKSIZE,
            low_memory=False,
            on_bad_lines="skip",
        ):
            for record in chunk.to_dict(orient="records"):
                yield record
                shard_count += 1
        dt = time.time() - t0
        print(f"  shard {sp.name}: {shard_count:>7,} rows ({dt:5.1f}s)")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = StatsAccumulator()

    with CORPUS_PATH.open("w") as out:
        # ---- YC ----
        yc_records = fetch_yc_records()
        for yc in yc_records:
            row = map_yc_row(yc)
            out.write(json.dumps(row, default=str) + "\n")
            stats.update(row)
        print(f"  wrote {stats.by_source['yc']} YC rows\n")

        # ---- Crunchbase ----
        print("Streaming Crunchbase shards...")
        t0 = time.time()
        for cb_record in iter_cb_rows():
            row = map_cb_row(cb_record)
            out.write(json.dumps(row, default=str) + "\n")
            stats.update(row)
        print(f"  wrote {stats.by_source['crunchbase']:,} CB rows ({time.time() - t0:.1f}s)\n")

    # ---- Stats ----
    stats_json = stats.to_json()
    with STATS_PATH.open("w") as f:
        json.dump(stats_json, f, indent=2)

    print("========= corpus_stats.json =========")
    print(json.dumps(stats_json, indent=2)[:2000])
    print()

    if stats.schema_violations:
        print(f"WARNING: {stats.schema_violations} rows missing required keys")
        sys.exit(2)


if __name__ == "__main__":
    main()
