"""
Run the full pipeline on the 10 demo seeds and save each result to
data/results/{slug}_results.json.

Requires:
    OPENAI_API_KEY  — for criterion extraction (GPT-4o) and verification
                      (GPT-4o-mini) + embeddings (text-embedding-3-small)
    CRUSTDATA_TOKEN — optional; enables step 2 enrichment

Usage:
    python -m scripts.run_all_seeds                 # all 10 seeds
    python -m scripts.run_all_seeds harvey notion   # subset by name
"""

import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.api import similar_to
from pipeline.seed_lookup import SeedAmbiguousError, SeedNotFoundError

RESULTS_DIR = REPO_ROOT / "data" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Each seed: (display_name, lookup_key, NL_query)
# - display_name: short label used to slug the output file
# - lookup_key: domain or explicit cb:/yc: record_id (for disambiguated seeds)
# - NL_query: the exact natural-language query the demo will be tested against
SEEDS = [
    ("Function Health", "functionhealth.com",
     "find healthcare AI companies like Function Health"),
    ("Replicate",       "replicate.com",
     "companies similar to Replicate in the ML infrastructure space"),
    ("Mercury",         "cb:28e465d4-ca50-43e9-8822-1707742b7c36",
     "what fintechs are like Mercury that bank startups?"),
    ("Hugging Face",    "huggingface.co",
     "show me open source AI tooling companies like Hugging Face"),
    ("Klarna",          "klarna.com",
     "European BNPL companies like Klarna"),
    ("Harvey",          "harvey.ai",
     "vertical AI applications like Harvey for legal"),
    ("Wiz",             "wiz.io",
     "cybersecurity startups like Wiz that focus on cloud security"),
    ("Vercel",          "vercel.com",
     "developer experience tools like Vercel"),
    ("Notion",          "cb:2f7760cc-4cdd-4dba-9ef6-0745e3420bab",
     "productivity software like Notion for technical teams"),
    ("Figma",           "figma.com",
     "design collaboration tools like Figma"),
]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "seed"


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set in env. Cannot run.")
        sys.exit(1)

    # CLI subset
    args = sys.argv[1:]
    if args:
        wanted = {a.lower() for a in args}
        seeds = [s for s in SEEDS if slugify(s[0]) in wanted or s[0].lower() in wanted]
        if not seeds:
            print(f"No seeds match: {args}")
            print(f"Available: {[slugify(s[0]) for s in SEEDS]}")
            sys.exit(1)
    else:
        seeds = list(SEEDS)

    print(f"Running {len(seeds)} seed(s)...\n")
    overall_t0 = time.time()
    summary = []
    for entry in seeds:
        name, key, user_query = entry
        slug = slugify(name)
        out_path = RESULTS_DIR / f"{slug}_results.json"
        print(f"\n{'=' * 72}")
        print(f"QUERY: {user_query!r}")
        print(f"SEED : {name}  (key={key})  → {out_path}")
        print('=' * 72)
        t_seed = time.time()
        try:
            result = similar_to(key, user_query=user_query, save_to=out_path)
            summary.append({
                "seed_name":              name,
                "slug":                   slug,
                "user_query":             user_query,
                "seconds":                round(time.time() - t_seed, 1),
                "n_tiers":                len(result["tiers"]),
                "tier1_size":             len(result["tiers"][0]["candidates"]) if result["tiers"] else 0,
                "candidates_total":       result["candidate_count_total"],
                "crustdata_matched":      result["meta"]["crustdata_matched"],
                "result_path":            str(out_path),
            })
        except (SeedNotFoundError, SeedAmbiguousError) as e:
            print(f"  SEED RESOLUTION FAILED: {e}")
            summary.append({"seed_name": name, "slug": slug, "error": str(e)})
        except Exception as e:
            print(f"  PIPELINE FAILED: {e}")
            import traceback; traceback.print_exc()
            summary.append({"seed_name": name, "slug": slug, "error": str(e)})

    elapsed = time.time() - overall_t0
    summary_path = RESULTS_DIR / "run_summary.json"
    summary_path.write_text(json.dumps({
        "elapsed_seconds": round(elapsed, 1),
        "seeds":           summary,
    }, indent=2))

    print(f"\n\n{'=' * 72}")
    print(f"OVERALL: {len(seeds)} seeds in {elapsed:.0f}s")
    print('=' * 72)
    for s in summary:
        if "error" in s:
            print(f"  ✗ {s['seed_name']:18s}  {s['error']}")
        else:
            print(f"  ✓ {s['seed_name']:18s}  {s['seconds']:>5.1f}s  "
                  f"tiers={s['n_tiers']}  tier1={s['tier1_size']}  "
                  f"crustdata={'yes' if s['crustdata_matched'] else 'no'}")
    print(f"\nSummary at {summary_path}")


if __name__ == "__main__":
    main()
