"""
CoreSimilar public API: similar_to(seed_domain_or_name, k=20)

Wires the seven-stage pipeline:
    seed_lookup -> crustdata_enrich -> criteria -> prefilter
    -> embedding (top-25) -> single LLM rerank -> rank/bucket -> output dict
"""

import json
import time
from pathlib import Path

from pipeline.seed_lookup import lookup_seed, _load_index, fetch_rows
from pipeline.crustdata_enrich import enrich
from pipeline.criteria import extract_criteria
from pipeline.prefilter import prefilter
from pipeline.embedding import embed_and_retrieve
from pipeline.verify import rerank_candidates
from pipeline.rank import rank_and_bucket, to_dict

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "data" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Top-K candidates fed into the single-call LLM reranker.
# Two-stage retrieval: cheap embedding recall + expensive LLM precision pass.
EMBED_TOP_K = 25


def similar_to(
    seed: str | None = None,
    *,
    user_query: str | None = None,
    domain: str | None = None,
    name: str | None = None,
    founded_year: int | None = None,
    country: str | None = None,
    embed_top_k: int = EMBED_TOP_K,
    save_to: Path | None = None,
    verbose: bool = True,
    # Accepted-but-ignored for back-compat with prior callers that passed batch size.
    verify_batch_size: int | None = None,
) -> dict:
    """Full pipeline entry point. Returns the structured output dict."""
    t_pipeline = time.time()

    # 1. Seed lookup
    if verbose:
        print(f"[1/7] Seed lookup: {seed or domain or name}")
    seed_rec = lookup_seed(seed, domain=domain, name=name,
                           founded_year=founded_year, country=country)
    if verbose:
        print(f"      -> {seed_rec.name} ({seed_rec.record_id})")
        print(f"         industries={seed_rec.industries[:5]} country={seed_rec.country}")

    # 2. Crustdata enrichment
    if verbose:
        print(f"[2/7] Crustdata enrichment for {seed_rec.domain}")
    enrichment = enrich(seed_rec.domain)
    if verbose:
        if enrichment and enrichment.matched:
            n = len(enrichment.competitors or [])
            print(f"      -> matched; {n} competitors, taxonomy {bool(enrichment.taxonomy)}")
        else:
            print(f"      -> no enrichment (token missing or no match) — falling back to local desc")

    # 3. Criterion extraction
    if verbose:
        print(f"[3/7] Criterion extraction via GPT-4o")
        if user_query:
            print(f"      user query: {user_query!r}")
    criteria = extract_criteria(seed_rec, enrichment, user_query=user_query)
    if verbose:
        for c in criteria:
            print(f"      [{c.id}] {c.text}")

    # 4. Categorical pre-filter
    if verbose:
        print(f"[4/7] Categorical pre-filter")
    pf = prefilter(seed_rec)
    if verbose:
        print(f"      industries={pf.seed_industries_used} -> {pf.n_final:,} candidates")

    # 5. Lazy embedding + cosine retrieval
    if verbose:
        print(f"[5/7] Lazy embedding (top-{embed_top_k})")
    emb_result = embed_and_retrieve(seed_rec, pf.candidate_ids, k=embed_top_k, verbose=verbose)
    if verbose:
        print(f"      -> top-{len(emb_result.top_k_ids)} retrieved "
              f"({emb_result.n_new_embeddings:,} new embeds, {emb_result.elapsed_seconds:.0f}s)")

    # 6. Single LLM rerank call on the top-K (was: per-candidate batched verification)
    if verbose:
        print(f"[6/7] Single rerank call on top-{len(emb_result.top_k_ids)}")
    candidate_scores = {
        rid: score for rid, score in zip(emb_result.top_k_ids, emb_result.top_k_scores)
    }
    rerank = rerank_candidates(
        seed_rec, criteria, emb_result.top_k_ids,
        cosine_scores=candidate_scores,
        verbose=verbose,
    )

    # 7. Rank + bucket + format
    if verbose:
        print(f"[7/7] Rank + bucket (LLM-assigned tiers)")
    idx = _load_index()
    # Augment compact meta with one_liner from full rows (only for top-K, so cheap).
    # description_full is the un-truncated description used by the UI hover tooltip.
    top_rows = fetch_rows(emb_result.top_k_ids)
    candidate_meta = {}
    for rid in emb_result.top_k_ids:
        base = dict(idx["compact"].get(rid, {}))
        row = top_rows.get(rid, {})
        long_desc = row.get("long_description") or ""
        one_liner = row.get("one_liner") or long_desc[:300]
        base["one_liner"] = one_liner
        base["description_full"] = long_desc or one_liner
        candidate_meta[rid] = base

    ranked = rank_and_bucket(
        seed_rec, criteria,
        emb_result.top_k_ids,
        candidate_scores,
        rerank.matches,
        candidate_meta,
        llm_tiers=rerank.tiers,
        tier_rationales=rerank.rationales,
    )
    out = to_dict(ranked)
    out["user_query"] = user_query
    out["pipeline_seconds"] = round(time.time() - t_pipeline, 1)
    out["meta"] = {
        "embed_top_k":           embed_top_k,
        "rerank_model":          rerank.model,
        "crustdata_matched":     bool(enrichment and enrichment.matched),
        "n_candidates_prefilter": pf.n_final,
        "n_new_embeddings":      emb_result.n_new_embeddings,
    }

    if save_to:
        save_to.write_text(json.dumps(out, indent=2))
        if verbose:
            print(f"\n[done] {out['pipeline_seconds']}s; wrote {save_to}")
    elif verbose:
        n_tiers = len(out["tiers"])
        print(f"\n[done] {out['pipeline_seconds']}s; {n_tiers} tiers, "
              f"{out['candidate_count_total']} candidates total")
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m pipeline.api <name_or_domain>")
        sys.exit(1)
    out = similar_to(sys.argv[1])
    print("\n=== TIERS ===")
    for t in out["tiers"][:3]:
        print(f"\n{t['tier_label']}  ({len(t['candidates'])} candidates)")
        for c in t["candidates"][:5]:
            print(f"  {c['cosine']:.3f}  {c['name'][:35]:35s} {c['domain']}")
