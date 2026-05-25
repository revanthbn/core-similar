"""
fetch_yc_seed.py — pull YC company data from yc-oss/api and prep the seed dataset.

Run once at the start of the project:
    python fetch_yc_seed.py

Outputs three files in data/raw/:
    yc_all.jsonl         — every YC company in the dataset (full record)
    yc_rebrands.jsonl    — only companies with former_names populated (your free rebrand labels)
    yc_seed.jsonl        — curated slice: W22-S24 batches (your main ER eval pool)
"""

import json
from collections import Counter
from pathlib import Path

import requests

YC_ALL_URL = "https://yc-oss.github.io/api/companies/all.json"
# YC OSS API returns full season strings ("Winter 2022"), not short codes ("W22").
# Window: 2019-2025 (14 batches).
SEED_BATCHES = {
    f"{season} {year}"
    for year in range(2019, 2026)
    for season in ("Winter", "Summer")
}
OUTPUT_DIR = Path("data/raw")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {YC_ALL_URL}...")
    resp = requests.get(YC_ALL_URL, timeout=60)
    resp.raise_for_status()
    companies = resp.json()
    print(f"  Total companies in YC dataset: {len(companies)}\n")

    # Rebrand candidates — companies with non-empty former_names
    rebrands = [c for c in companies if c.get("former_names")]
    print(f"Companies with former_names (FREE rebrand labels): {len(rebrands)}")
    print("Sample rebrands:")
    for c in rebrands[:15]:
        former = ", ".join(c["former_names"])
        print(f"  {c['name']:35s}  was: {former}")
    print()

    # Status distribution — useful for sampling
    status_counts = Counter(c.get("status", "Unknown") for c in companies)
    print("Status distribution:")
    for status, n in status_counts.most_common():
        print(f"  {status:15s}  {n}")
    print()

    # Batch counts for seed window
    batch_counts = Counter(c["batch"] for c in companies)
    print("Seed batch coverage (W22-S24):")
    seed_total = 0
    for batch in sorted(SEED_BATCHES):
        n = batch_counts.get(batch, 0)
        seed_total += n
        print(f"  {batch}: {n}")
    print(f"  Total in seed window: {seed_total}\n")

    # Industry distribution — informs sampling diversity
    industries = Counter(c.get("industry", "Unknown") for c in companies)
    print("Top 10 industries (full dataset):")
    for ind, n in industries.most_common(10):
        print(f"  {ind:35s}  {n}")
    print()

    # ---- Save outputs ----
    all_path = OUTPUT_DIR / "yc_all.jsonl"
    with all_path.open("w") as f:
        for c in companies:
            f.write(json.dumps(c) + "\n")
    print(f"Saved {all_path}  ({len(companies)} records)")

    rebrand_path = OUTPUT_DIR / "yc_rebrands.jsonl"
    with rebrand_path.open("w") as f:
        for c in rebrands:
            # Minimal projection for the rebrand pair file
            f.write(json.dumps({
                "current_name": c["name"],
                "former_names": c["former_names"],
                "slug": c["slug"],
                "batch": c["batch"],
                "status": c["status"],
                "website": c.get("website"),
                "one_liner": c.get("one_liner"),
                "long_description": c.get("long_description"),
                "industry": c.get("industry"),
                "yc_url": c.get("url"),
                "all_locations": c.get("all_locations"),
            }) + "\n")
    print(f"Saved {rebrand_path}  ({len(rebrands)} records) ← your rebrand category, pre-labeled")

    seed = [c for c in companies if c["batch"] in SEED_BATCHES]
    seed_path = OUTPUT_DIR / "yc_seed.jsonl"
    with seed_path.open("w") as f:
        for c in seed:
            f.write(json.dumps(c) + "\n")
    print(f"Saved {seed_path}  ({len(seed)} records) ← your main ER eval pool")


if __name__ == "__main__":
    main()
