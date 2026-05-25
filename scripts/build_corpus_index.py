"""
One-time index build over data/raw/company_corpus.jsonl.

Produces data/cache/corpus_index.pkl with everything downstream pipeline
stages need for fast random access:

  domain_to_records    : domain -> [record_id]
  name_to_records      : name_norm -> [record_id]
  industry_to_records  : industry_token -> [record_id]   (inverted index)
  record_offsets       : record_id -> byte offset in JSONL
  compact              : record_id -> {source, name, domain, name_norm,
                                        founded_year, operating_status,
                                        country, num_employees_enum,
                                        team_size, industries (set),
                                        has_description}

Lookup pattern:
    1. Load the pickle (~10s, ~1GB resident)
    2. Query by domain / name / industry to get record_id sets
    3. Use record_offsets + file.seek to fetch full row text only for
       records that actually need it (descriptions for embedding etc.)

Run once:
    python -m scripts.build_corpus_index
"""

import json
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO_ROOT / "data" / "raw" / "company_corpus.jsonl"
INDEX_PATH = REPO_ROOT / "data" / "cache" / "corpus_index.pkl"

COMPACT_FIELDS_FROM_ROW = (
    "source", "name", "domain", "name_norm",
    "founded_year", "operating_status", "country",
    "num_employees_enum", "team_size",
)


def main():
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Building index from {CORPUS_PATH}")
    t0 = time.time()

    domain_to_records = defaultdict(list)
    name_to_records = defaultdict(list)
    industry_to_records = defaultdict(list)
    record_offsets = {}
    compact = {}

    n = 0
    with CORPUS_PATH.open("rb") as f:
        while True:
            offset = f.tell()
            line = f.readline()
            if not line:
                break
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = r.get("record_id")
            if not rid:
                continue
            n += 1

            record_offsets[rid] = offset

            row_compact = {k: r.get(k) for k in COMPACT_FIELDS_FROM_ROW}
            inds = r.get("industries") or []
            row_compact["industries"] = list(inds)
            # has_description flag for downstream cost decisions
            has_desc = bool(r.get("one_liner") or r.get("long_description"))
            row_compact["has_description"] = has_desc
            compact[rid] = row_compact

            if r.get("domain"):
                domain_to_records[r["domain"]].append(rid)
            if r.get("name_norm"):
                name_to_records[r["name_norm"]].append(rid)
            for ind in inds:
                if ind:
                    industry_to_records[ind].append(rid)

            if n % 250_000 == 0:
                print(f"  indexed {n:,} rows ({time.time() - t0:.0f}s)")

    print(f"\nIndexed {n:,} rows in {time.time() - t0:.0f}s")
    print(f"  unique domains   : {len(domain_to_records):,}")
    print(f"  unique name_norm : {len(name_to_records):,}")
    print(f"  unique industries: {len(industry_to_records):,}")

    payload = {
        "corpus_path":          str(CORPUS_PATH),
        "domain_to_records":    dict(domain_to_records),
        "name_to_records":      dict(name_to_records),
        "industry_to_records":  dict(industry_to_records),
        "record_offsets":       record_offsets,
        "compact":              compact,
        "n_records":            n,
    }

    print(f"\nPickling to {INDEX_PATH}...")
    t0 = time.time()
    with INDEX_PATH.open("wb") as out:
        pickle.dump(payload, out, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = INDEX_PATH.stat().st_size / (1024 * 1024)
    print(f"  wrote {size_mb:,.0f} MB in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
