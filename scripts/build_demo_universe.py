"""
Trim the full 2.8M corpus to a demo-relevant subset based on the industries
that appear on the 10 seed queries.

Logic:
  1. Resolve each of the 10 seed (name, domain) pairs against the full index
  2. Union all their industries
  3. Manually expand the union with adjacent industries we know matter for
     this artifact (AI variants, dev-tools variants, etc.)
  4. Keep CB rows that:
       - have at least one industry in the union, AND
       - have operating_status = "active", AND
       - have_description = True
  5. ALWAYS keep all YC rows (3,494 of them — tiny, and their industry tags
     don't always overlap with CB tags but their peers should be in scope)
  6. ALWAYS keep the 10 seed records themselves, even if they wouldn't pass
     the filter
  7. Write data/cache/demo_universe.jsonl + data/cache/demo_index.pkl

Outputs:
  data/cache/demo_universe.jsonl       — trimmed corpus
  data/cache/demo_index.pkl            — index over the trimmed corpus
                                          (same schema as corpus_index.pkl)
  data/cache/demo_universe_stats.json  — counts, industries kept, by-source mix
"""

import json
import pickle
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FULL_INDEX_PATH = REPO_ROOT / "data" / "cache" / "corpus_index.pkl"
CORPUS_PATH = REPO_ROOT / "data" / "raw" / "company_corpus.jsonl"
DEMO_CORPUS_PATH = REPO_ROOT / "data" / "cache" / "demo_universe.jsonl"
DEMO_INDEX_PATH = REPO_ROOT / "data" / "cache" / "demo_index.pkl"
DEMO_STATS_PATH = REPO_ROOT / "data" / "cache" / "demo_universe_stats.json"

# Each seed = (display_name, lookup_key). lookup_key is a domain or
# explicit cb:/yc: record_id for the cases we had to disambiguate by hand.
DEMO_SEEDS = [
    ("Function Health", "functionhealth.com"),
    ("Replicate",       "replicate.com"),
    ("Mercury",         "cb:28e465d4-ca50-43e9-8822-1707742b7c36"),  # Banking/FinTech 2017
    ("Hugging Face",    "huggingface.co"),
    ("Klarna",          "klarna.com"),
    ("Harvey",          "harvey.ai"),
    ("Wiz",             "wiz.io"),
    ("Vercel",          "vercel.com"),
    ("Notion",          "cb:2f7760cc-4cdd-4dba-9ef6-0745e3420bab"),  # Notion Labs 2012
    ("Figma",           "figma.com"),
]

# Industries kept for the demo corpus. Specifically EXCLUDES super-broad
# tags (Software, B2B, Apps, Internet, E-Commerce, Information Technology,
# Consumer Goods) because they pull in 200K-500K rows each that are mostly
# noise. The narrow tags below still catch peers via at-least-one-overlap.
INDUSTRY_WHITELIST = {
    # AI / ML
    "Artificial Intelligence (AI)", "Machine Learning", "Generative AI",
    "Natural Language Processing", "Computer Vision", "Deep Learning",
    "Predictive Analytics", "Speech Recognition",
    # Dev tools / infra
    "Developer APIs", "Developer Platform", "Developer Tools", "DevOps",
    "Cloud Computing", "Cloud Infrastructure", "Cloud Management",
    "Cloud Security", "Cloud Storage", "Infrastructure",
    # Health
    "Health Care", "Digital Health", "Telehealth", "Medical",
    "Health Diagnostics", "Wellness", "mHealth", "Therapeutics",
    "Biotechnology", "Genetics",
    # Fintech
    "FinTech", "Financial Services", "Banking", "Payments",
    "Mobile Payments", "Credit", "Credit Cards", "Wealth Management",
    "InsurTech", "Insurance",
    # Security
    "Cyber Security", "Information Security", "Network Security",
    "Identity Management", "Compliance", "Fraud Detection",
    # Productivity / collab
    "Productivity Tools", "Collaboration", "Project Management",
    "Workflow Automation", "Knowledge Management",
    # Vertical AI
    "Legal", "Legal Tech", "RegTech",
    # Design
    "Product Design", "Graphic Design", "Web Design", "UX Design",
    # Other useful narrows
    "Open Source", "Enterprise Software", "SaaS", "Mobile Apps",
    "Foundation Models",
}


def main():
    print(f"Loading full index from {FULL_INDEX_PATH}")
    t0 = time.time()
    with FULL_INDEX_PATH.open("rb") as f:
        idx = pickle.load(f)
    print(f"  loaded in {time.time() - t0:.0f}s; {idx['n_records']:,} records\n")

    # ---- Resolve seeds and collect their industries ----
    print("Resolving 10 seeds and collecting their industries...")
    seed_record_ids = set()
    seed_industries = Counter()
    seed_resolution_status = []
    for name, lookup_key in DEMO_SEEDS:
        # Explicit record_id override (for hand-disambiguated seeds)
        if lookup_key.startswith(("cb:", "yc:")):
            rid = lookup_key if lookup_key in idx["compact"] else None
        else:
            rids = idx["domain_to_records"].get(lookup_key, [])
            rid = rids[0] if rids else None
        if rid is None:
            seed_resolution_status.append((name, lookup_key, "not_found", None))
            print(f"  {name:18s} {lookup_key:50s} -> NOT FOUND")
            continue
        meta = idx["compact"][rid]
        for ind in meta.get("industries", []):
            seed_industries[ind] += 1
        seed_record_ids.add(rid)
        seed_resolution_status.append((name, lookup_key, "ok", rid))
        print(f"  {name:18s} {lookup_key[:50]:50s} -> {rid[:55]} industries={meta.get('industries', [])[:4]}")

    print(f"\n  resolved {len(seed_record_ids)}/{len(DEMO_SEEDS)} seeds")
    print(f"  industries unioned across seeds (top 20):")
    for ind, n in seed_industries.most_common(20):
        print(f"    {n}x  {ind}")

    # Final industry set = whitelist + any seed-derived tags that are
    # specific (not in the broad-tag drop list).
    BROAD_DROP = {
        "Software", "B2B", "Apps", "Internet", "Information Technology",
        "E-Commerce", "Consumer Goods", "Consumer", "Service Industry",
        "Marketing", "Media and Entertainment",
    }
    seed_specific = {ind for ind in seed_industries if ind not in BROAD_DROP}
    industries_kept = INDUSTRY_WHITELIST | seed_specific
    print(f"\n  total industries in scope: {len(industries_kept)}")

    # ---- Build candidate set: industry overlap + active + has_description ----
    print("\nBuilding candidate set...")
    # Start with union of industry inverted index entries
    candidate_rids = set()
    for ind in industries_kept:
        candidate_rids.update(idx["industry_to_records"].get(ind, []))
    print(f"  union via industries: {len(candidate_rids):,} rows")

    # Filter: active CB + has_description + founded >= 2010; always keep
    # YC + always keep seeds. The founded_year cutoff trims pre-modern-SaaS
    # companies that flood broad tags like "Health Care" and "FinTech".
    FOUNDED_YEAR_FLOOR = 2010
    keep = set()
    by_source = Counter()
    for rid in candidate_rids:
        meta = idx["compact"][rid]
        if meta["source"] == "yc":
            keep.add(rid)
        elif meta["source"] == "crunchbase":
            if (meta.get("operating_status") == "active"
                    and meta.get("has_description")
                    and meta.get("founded_year") is not None
                    and meta["founded_year"] >= FOUNDED_YEAR_FLOOR):
                keep.add(rid)

    # Add all YC rows unconditionally
    yc_n_added = 0
    for rid, meta in idx["compact"].items():
        if meta["source"] == "yc" and rid not in keep:
            keep.add(rid)
            yc_n_added += 1

    # Add seeds explicitly
    for rid in seed_record_ids:
        keep.add(rid)

    for rid in keep:
        by_source[idx["compact"][rid]["source"]] += 1
    print(f"  after filters: {len(keep):,} rows ({by_source['crunchbase']:,} CB + {by_source['yc']:,} YC)")
    print(f"    (all {yc_n_added + sum(1 for _, m in idx['compact'].items() if m['source']=='yc' and m.get('industries') and any(i in industries_kept for i in m['industries']))} YC rows force-kept)")

    # ---- Materialize the demo corpus JSONL via byte-offset reads ----
    print(f"\nMaterializing {DEMO_CORPUS_PATH}...")
    t0 = time.time()
    # Sort kept rids by offset for sequential disk access
    kept_sorted = sorted(keep, key=lambda rid: idx["record_offsets"].get(rid, -1))
    full_corpus = CORPUS_PATH.open("rb")
    n_written = 0
    new_offsets = {}
    demo_domain_index = defaultdict(list)
    demo_name_index = defaultdict(list)
    demo_industry_index = defaultdict(list)
    demo_compact = {}

    with DEMO_CORPUS_PATH.open("wb") as out:
        for rid in kept_sorted:
            off = idx["record_offsets"].get(rid)
            if off is None:
                continue
            full_corpus.seek(off)
            line = full_corpus.readline()
            new_offsets[rid] = out.tell()
            out.write(line)
            n_written += 1
            # Build new indices on the fly using compact
            meta = idx["compact"][rid]
            if meta.get("domain"):
                demo_domain_index[meta["domain"]].append(rid)
            if meta.get("name_norm"):
                demo_name_index[meta["name_norm"]].append(rid)
            for ind in meta.get("industries", []):
                if ind:
                    demo_industry_index[ind].append(rid)
            demo_compact[rid] = meta
    full_corpus.close()
    elapsed = time.time() - t0
    size_mb = DEMO_CORPUS_PATH.stat().st_size / (1024 * 1024)
    print(f"  wrote {n_written:,} rows ({size_mb:.0f} MB) in {elapsed:.0f}s")

    # ---- Pickle demo index ----
    print(f"\nPickling demo index to {DEMO_INDEX_PATH}...")
    payload = {
        "corpus_path":          str(DEMO_CORPUS_PATH),
        "domain_to_records":    dict(demo_domain_index),
        "name_to_records":      dict(demo_name_index),
        "industry_to_records":  dict(demo_industry_index),
        "record_offsets":       new_offsets,
        "compact":              demo_compact,
        "n_records":            n_written,
    }
    with DEMO_INDEX_PATH.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  demo_index.pkl size: {DEMO_INDEX_PATH.stat().st_size / (1024*1024):.0f} MB")

    # ---- Stats ----
    stats = {
        "n_rows": n_written,
        "by_source": dict(by_source),
        "n_industries_kept": len(industries_kept),
        "industries_kept": sorted(industries_kept),
        "seed_resolution": [
            {"name": n, "domain": d, "status": s, "record_id": r}
            for n, d, s, r in seed_resolution_status
        ],
        "top_industries_via_seeds": seed_industries.most_common(30),
    }
    with DEMO_STATS_PATH.open("w") as f:
        json.dump(stats, f, indent=2)
    print(f"\nWrote {DEMO_STATS_PATH}")


if __name__ == "__main__":
    main()
