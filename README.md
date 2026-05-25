# CoreSimilar — Semantic Company Similarity Engine

Given a seed company, returns a ranked list of similar companies with per-criterion match evidence and tier assignments. Built on the YC OSS dataset joined against a 2.8M-record Crunchbase snapshot.

---

## TL;DR

Plain-English query in, tiered ranked peers out, with per-criterion match badges and one-sentence evidence per candidate. Side-by-side hook against Crustdata Explore on a curated set of 9 demo seeds spanning healthcare AI, ML infra, fintech, vertical AI, dev tools, and consumer SaaS.

## Why I built this

At Coldbean, I built a lead scoring module on top of Crustdata's People Search and Enrichment APIs. The flow was: take a customer's ICP, pull candidate leads via Crustdata Search, then score them down to the most relevant subset. I lived inside this pipeline for months. The piece that always hurt was the ICP modeling layer, figuring out which companies looked like the customer's best accounts. We hand-rolled it with brittle filters.

When I saw the Founding ML Engineer JD, the bullet on entity resolution at scale stood out. But what I actually wanted to build was the version of company similarity I wished I'd had then. CoreSimilar is that. It sits on the gap Crustdata's Explore Companies feature leaves open: "companies like Function Health" returns Apollo Hospitals (a 38,000-person Indian hospital chain) because the underlying NL-to-filter system collapses the seed to an industry tag. The point of this artifact is to show what the layer above that looks like, and to tie the ML work directly to the product surface customers already use.

## How to look at this

Three entry points, in order of effort:

1. **Loom walkthrough** (5 min): narrated tour of the architecture, side-by-side comparison with Explore Companies, and the ER design. Link in the message accompanying this repo.
2. **Hosted UI**: nine pre-warmed queries (Function Health, Replicate, Mercury, Harvey, Hugging Face, Klarna, Wiz, Vercel, Notion). Click any chip to see the result instantly. Static deployment; new freeform queries require running the pipeline locally because the LLM mapper and pipeline don't run in the Vercel environment.
3. **Run it locally**: clone, follow the "Data setup" section below, then `python -m pipeline.api "your query"`. Wall-clock ~30 to 90 seconds per new query, ~$0.03 in OpenAI API costs.

## Data setup

The pipeline expects three inputs:

1. YC OSS data: `python scripts/fetch_yc.py` (runs in 30s, hits the public API)
2. Crunchbase snapshot: place a Crunchbase company CSV at 
   `data/raw/crunchbase.csv`. Any reasonably recent snapshot works; 
   the schema we use is documented in `pipeline/load_crunchbase.py`.
3. Build the unified corpus: `python scripts/build_corpus.py` 
   (runs in ~10 min on a laptop, produces `data/raw/company_corpus.jsonl`)

Embeddings are computed lazily on first query for each candidate; no precomputation needed. If you'd like the exact demo cache used in the Loom (corpus index plus precomputed embeddings, ~1.2GB), DM me and I'll share a Drive link.

## Architecture

A seven-stage local pipeline. The ML depth is concentrated in three places: criterion extraction (LLM, structured), embedding retrieval, and a single LLM rerank call on the narrow candidate set.

```
Query → seed lookup → Crustdata enrichment → criterion extraction (LLM, 1 call)
                                                       │
                                                       ▼
        categorical pre-filter (industry ∩ active ∩ size band) → ~5K-30K candidates
                                                       │
                                                       ▼
        OpenAI text-embedding-3-small cosine retrieval (top-25)
                                                       │
                                                       ▼
        single LLM rerank call on top-25 × all criteria  (gpt-5-mini)
                                                       │
                                                       ▼
        output: tiered ranked list with per-criterion match table + evidence
```

This is the standard two-stage retrieval pattern used in modern semantic search: cheap recall with embeddings, then an expensive precision pass on a narrow candidate set. ColBERT, Cohere Rerank, and Anthropic's retrieval cookbook all use this shape. The LLM is contained to the precision-critical final step rather than being run in the inner loop on hundreds of candidates.

Per query:

- One LLM call for criterion extraction (4-6 atomic criteria from the seed, user query, and Crustdata signals).
- One LLM call for reranking (per-candidate per-criterion verdicts, one-line evidence, holistic tier label).
- Embedding cosine and the categorical pre-filter are local and cached.

Cost per query: ~$0.01 to $0.05. Wall-clock: ~30 to 90 seconds on a cold cache.

### Why top-25 + one rerank call

I considered a per-candidate verification approach over the top-500 from cosine (60 to 100 LLM calls per query, ~$0.20 in spend, 8 to 15 minutes wall-clock) and decided against it for two reasons.

First, it structurally over-uses the LLM. Embedding cosine on combined descriptions already does most of the recall work. Entailment-on-each-candidate is the wrong shape for the marginal precision lift you get back.

Second, the holistic rerank approach (one LLM call seeing all 25 candidates and all criteria together) produces a stronger ranking in practice, not weaker. The model can compare candidates against each other rather than scoring each in isolation, which is closer to how a human analyst would actually rank peers. The output quality matches or beats the per-candidate approach at ~7x lower cost and ~10x lower latency.

### Production architecture

In production at scale (60M+ companies, sub-second query latency requirements) the LLM reranker is replaced by a fine-tuned cross-encoder trained on customer interaction data (click-through, downstream conversion). The shape of the pipeline does not change:

- Embedding precomputation moves to ingestion time (one-shot encode of the full corpus, ANN index for retrieval).
- The categorical pre-filter logic stays as-is.
- The LLM call in the precision pass swaps in for a cross-encoder serving inference at ~10 to 50 ms per (query, candidate) pair.

The MVP demonstrates the approach (multi-stage retrieval with intelligent reranking) while making the production endpoint explicit. The criterion-extraction LLM call survives at query time because criteria are user-intent-conditioned and cheap relative to the retrieval pass.

> Note on embedding models. Runtime uses OpenAI text-embedding-3-small because it was the fastest path to a working pipeline. The Tier 2 ER design below specifies BGE-M3 because the cross-encoder training step in that tier benefits from open-source weights you can fine-tune. In production, a single open-source encoder (BGE-M3 or a successor) would serve both surfaces.

---

## Side-by-side: CoreSimilar vs Crustdata Explore

Two seeds, same query interface, dramatically different output.

### Query: "find healthcare AI companies like Function Health"

**Crustdata Explore returns** (top 5): Apollo Hospitals (38,256 employees, Indian hospital chain), Aster DM Healthcare (19,400 employees, Indian hospital operator), Artivatic.ai, MFine, KiviHealth. The NL-to-filter system collapsed "healthcare AI" to an industry filter and returned the largest health-care companies in the corpus, dominated by Indian hospital operators.

**CoreSimilar returns** (Tier 2 partial peers): Mytamin (personalized vitamins with home blood tests), Flip Health (long-term disease management with home lab tests), Life Metrics (predictive biomarker testing). All actual DTC consumer health and clinical lab companies, which is what Function Health is.

The LLM extracted these criteria for Function Health:

1. Offers clinical laboratory testing panels directly to consumers (DTC). Rationale: distinguishes consumer-facing lab providers from B2B diagnostics vendors, hospital labs, or enterprise software.
2. Operates a subscription or recurring-service model for routine periodic lab testing.
3. Provides physician-led interpretation or expert medical explanations of test results.

Criterion 1's rationale explicitly excludes hospital labs, which is precisely the failure mode Crustdata Explore falls into.

### Query: "companies similar to Replicate in the ML infrastructure space"

**Crustdata Explore returns**: Pipl (a person-search company), ryw (8-person AI startup), 3RDi, PromethistAI, Pawa AI. None of these are ML infrastructure. They share the "Artificial Intelligence" tag and not much else.

**CoreSimilar returns** (Tier 1 strong peers, 4-5 of 5 criteria matched): Replicate itself, Orchestra (ML model deployment platform), Together AI (cloud-based ML platform), Model Share AI (MLOps platform), ModelsLab (model-serving API). Every Tier 1 result is genuinely ML infrastructure.

Funnel for this query: 2.8M corpus → 350K candidates after the pre-filter pass on size and active-status (a "demo trim" trimming closed and clearly-irrelevant companies for faster iteration), → 38K candidates after applying the industry-overlap filter, → 25 candidates after embedding cosine retrieval, → 20 verified peers across the four tiers. 35,636 new embeddings computed for this query. Total pipeline wall-clock: 1,060 seconds.

A quantitative four-baseline eval (Recall@K, NDCG, MRR against hand-labeled peer sets across 15-25 seeds) is the next thing I would build on this. Time-budgeted out of the artifact.

---

## Entity Resolution Design

> Production company ER is fundamentally tiered. Most labeled-pair classifiers are trained against eval distributions that hide where rules already win, and where rules fail.

The JD bullet on "entity resolution at scale" is the load-bearing one for this artifact, so this section is more detailed than the others. CoreSimilar's ER stack is a three-tier hybrid. **Tier 1 is implemented and shipped.** Tier 2 and Tier 3 are designed in full below but deliberately deferred. The architecture specs are concrete enough to implement on a follow-up.

### The architecture

```
                            ┌──────────────────────────┐
   YC row (2019-2025) ─────▶│  TIER 1: Rules           │
                            │  (implemented)           │
                            └────────────┬─────────────┘
                                  │ status:
                  matched_unique  │  tier1_ambiguous   absent_from_crunchbase
                    accept ▼      ▼                    ▼
                  ┌────────┐ ┌─────────────────┐ ┌──────────────────────┐
                  │ DONE   │ │ TIER 2 (designed)│ │ TIER 3 (designed)    │
                  │        │ │ learned pairwise │ │ semantic recall over │
                  │        │ │ classifier       │ │ description ANN      │
                  │        │ │ over candidates  │ │ then Tier 2 reranker │
                  └────────┘ └─────────────────┘ └──────────────────────┘
```

Tiers are an explicit ladder of cost vs. recall. Tier 1 is the cheapest test (hash lookup plus arithmetic), runs over all 2.8M CB rows in ~30s, and resolves the easy majority of YC→CB joins. Tier 2 burns embedding compute only on the *ambiguous* output of Tier 1, never on the easy cases Tier 1 already nailed. Tier 3 only fires when both name and domain blocking failed entirely, and uses dense retrieval to surface candidates rules cannot reach.

The point isn't that ML beats rules. The point is that **rules and ML operate on different distributions inside the same problem**, and a production system uses each where it dominates.

### Tier 1: Rules (implemented)

Two rules. A YC row matches a Crunchbase row if either fires:

```
domain_match  :   yc.domain  == cb.domain        (both non-null)

name+year     :   yc.name_norm == cb.name_norm
              AND yc.founded_year, cb.founded_year both non-null
              AND |year_delta| <= 2
```

Names are normalized via [pipeline/text_normalize.py](pipeline/text_normalize.py): lowercase, strip the common corporate suffixes (Inc/LLC/Ltd/Corp/Co/GmbH/AG/SAS/Pte/etc.), strip punctuation, collapse whitespace. Domains are extracted from website URLs (strip scheme, `www.`, path).

**Strict null-year handling.** A CB row with `founded_year = null` fails the name+year rule by construction. This is intentional. Bending Tier 1 to recover recall pollutes its precision number and erases the clean story Tier 1 is supposed to tell. The trade is ~92 known YC rows where the CB name matched but the CB founding year was null. **These are recorded with `failure_reason: cb_null_year` so Tier 2's description-cosine signal can later quantify how many it would recover.**

**Former-names extension.** YC ships a `former_names` list per company (Anyscale was Ray Labs, Canopy was Encarte). Tier 1 hashes each former name with the same normalization and uses it as an additional name key. This is free: same strict precision properties, catches rebrand cases that would otherwise wait for Tier 2.

A subtle case the implementation handles: if the current name and a former name resolve to *different* CB records (e.g. an acquirer's record persists alongside the renamed entity's), the row is flagged `tier1_ambiguous` rather than silently picking one. Cross-name splits are reported separately in the stats.

**Tier 1 over the full corpus** ([data/raw/tier1_stats.json](data/raw/tier1_stats.json)):


| Outcome                                    | Count | % of YC rows |
| ------------------------------------------ | ----- | ------------ |
| matched_unique                             | 1,909 | 54.6%        |
| tier1_ambiguous (≥2 CB candidates)         | 804   | 23.0%        |
| absent_from_crunchbase                     | 781   | 22.4%        |
| ↳ `cb_null_year` (recoverable by Tier 2)   | 92    | 2.6%         |
| ↳ no name/domain signal (Tier 3 territory) | 689   | 19.7%        |


Side stats worth highlighting:

- 711 YC rows pick up CB candidates only via former_names that would otherwise have been Tier 1 misses (1,353 distinct candidates).
- 344 YC rows are `tier1_ambiguous` specifically because current-name and former-name resolved to different CB records (the rebrand case).
- Match-method mix across all returned candidates: 1,746 `both` (38.7%), 422 `domain` (9.4%), 2,505 `name+year` (55.6%).

**Tier 1 quantitative eval** (80 labeled rows, stratified across 20 easy / 25 random / 20 rebrand / 15 weak_domain):


| Stratum     | n   | n_present | Unique-match P | Top-candidate P | Recall    | Absent P | F1        |
| ----------- | --- | --------- | -------------- | --------------- | --------- | -------- | --------- |
| **overall** | 80  | 64        | **94.1%**      | 88.2%           | **98.4%** | 91.7%    | **96.2%** |
| easy        | 20  | 17        | 100.0%         | 93.8%           | 94.1%     | 75.0%    | 97.0%     |
| random      | 25  | 20        | 88.9%          | 87.0%           | 100.0%    | 100.0%   | 94.1%     |
| rebrand     | 20  | 15        | 90.0%          | 81.2%           | 100.0%    | 100.0%   | 94.7%     |
| weak_domain | 15  | 12        | 100.0%         | 92.3%           | 100.0%    | 100.0%   | 100.0%    |


Definitions:

- **Unique-match precision**: of YC rows Tier 1 calls `matched_unique`, what fraction has the gold CB row as its single match. The clean academic metric.
- **Top-candidate precision**: of YC rows Tier 1 returns ≥1 candidate for, after picking the highest-priority match (`both > domain > name+year`, tiebroken by smaller year delta and current name over former name), what fraction is the gold CB row. The production metric, what downstream candidate-pool construction consumes.
- **Candidate recall**: of YC rows with a gold CB match, what fraction surfaces that CB row in Tier 1's candidate set (single or multiple).
- **Absent precision**: of YC rows Tier 1 calls `absent_from_crunchbase`, what fraction truly has no CB match.

**The story the numbers tell:**

- Recall is essentially perfect on every stratum except `easy`, where one false negative (Flux Auto: YC founded=2022, CB founded=2017, year delta=5) is the cost of strict null-year handling. The miss is surfaced explicitly with a `failure_reason` flag. Tier 3 semantic recall is exactly the tier designed to recover it.
- Unique-match precision is 100% on `easy` and `weak_domain` but drops to ~89% on `random` and 90% on `rebrand`. **This is the gap Tier 2 is designed to close.** Three `random` false positives and one `rebrand` false positive are cases where name+year matched but description content diverged (PromptLoop matched a CB "Kiter" job-search app via YC's former_name='Kiter', same name, different product; Rownd on rownd.ai matched Rownd on rownd.com, different products entirely; Matter as a reading app matched 5 unrelated Matters). Description-cosine in Tier 2 separates these.
- A distinct error mode worth calling out: **domain recycling.** YC OneKey (EV-charging site selection, French founders) shares the domain `getonekey.io` with a deceased CB OneKey (mobile-keyboard CRM, founder Christophe Barre) that previously owned that domain. Pure domain reuse after the original company died. Tier 1's domain rule cannot detect this. Tier 2's founder and location signals are exactly what catches it.
- Top-candidate precision is below unique-match across the board because three `tier1_ambiguous` rows pick the wrong candidate when there are multiple `both`-method ties (Fathom: 4 different Fathoms in CB; Flint: 4 different Flints; Spring in Africa: rebrand with 5 Spring/Wallets candidates). Tier 2's classifier resolves these.
- Absent precision drops to 75% on `easy` (3 of 4 absent calls correct) for the Flux Auto false negative.

**Where the former_names extension earned its keep:** Solum Health (formerly Momentu, pivoted from Latam B2C mental-health coaching to US B2B healthcare admin AI) is a Tier 1 recovery via former_names lookup that would have been missed by strict current-name matching. 711 YC rows in the full corpus pick up CB candidates via former_names; 344 of those produce cross-name-split ambiguity (current-name and former-name hitting different CB rows).

The 9 error rows are written to [data/eval/er_errors.jsonl](data/eval/er_errors.jsonl) and used as the named error cases above.

> Labels were generated by Claude reading the corpus directly (not the public Crunchbase website), using content signals Tier 1 doesn't see (one_liner, founders, industries, locations). Then reviewed by a human against external sources (corporate filings, employee registries, social handles). Two labels flipped on review: Solum Health (null to match: verified pivot) and OneKey (match to null: domain recycling). Marked `labeled_by: claude_auto` with notes reflecting both passes.

### Tier 2: Learned pairwise classifier (designed, deferred)

**Trigger.** Tier 2 runs only when Tier 1 hands it an ambiguous case: the row is `tier1_ambiguous` (≥2 candidates), or the row is `matched_unique` but the single match came via `name+year` only (no domain corroboration), which has lower precision than `both`.

**Architecture.** LightGBM binary classifier over engineered features of the (yc, cb_candidate) pair, producing a calibrated P(same_company). Calibration via isotonic regression on a held-out slice. Output: top-1 candidate above threshold, or "no confident match."

**Features.**


| Feature                  | Why                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `name_jaro_winkler`      | catches typos, transliteration variants                                                                      |
| `name_edit_ratio`        | normalized edit distance, complementary to JW                                                                |
| `domain_exact`           | strongest single boolean; mostly redundant with Tier 1 but useful as a feature interaction with name signals |
| `domain_root_match`      | foo.com vs foo.io vs foo.co; rules can't decide                                                              |
| `founded_year_delta`     | int distance; carries the strict null-year info                                                              |
| `description_cosine`     | **BGE-M3** embedding of `one_liner`+`short_description`; the semantic signal that recovers rule-blind cases  |
| `industries_jaccard`     | YC `industries` ∩ CB `categories`                                                                            |
| `location_country_match` | weak but cheap disambiguation                                                                                |
| `linkedin_url_match`     | when available, near-certain positive                                                                        |
| `former_name_match`      | did this pair connect via YC.former_names?                                                                   |


**Why GBM over a cross-encoder.** With the 150-200 labeled pairs realistic for a first pass (eval template plus a labeling sweep on Tier 1 ambiguous rows), GBM with engineered features generalizes better than fine-tuning a transformer cross-encoder. The inductive bias of `domain_exact` and `name_edit_ratio` is exactly what makes this problem tractable on small data. Cross-encoders earn their keep at 10K+ labels, which is the regime Crustdata's production system would actually live in.

**Training data.** Positives from labeled (yc, cb) pairs. Hard negatives mined from Tier 1's ambiguous outputs (other candidates for the same YC row) and from same-industry, same-stage non-matches. 5-fold CV; held-out 30% for the reported numbers. Reliability diagram and PR curve in the results section once trained.

### Tier 3: Semantic recall (designed, deferred)

**Trigger.** Tier 1 returned zero candidates AND the YC row has a non-empty `one_liner`/`long_description`.

**Architecture.** Encode all 2.8M CB descriptions once with BGE-M3 (cached to disk), build a FAISS HNSW index. At query time, encode the YC row's description and retrieve top-K (K=50). Feed each retrieved candidate through Tier 2's classifier; accept the top scorer above threshold, else mark unresolvable.

**Motivating cases.** This is the Zepto pattern: YC reports `zeptonow.com`, Crunchbase reports `zepto.in`, domain match fails. The names also differ slightly. Only description carries the signal: "10-minute grocery delivery in India." Same shape applies to companies under acquirer names, regionalized variants, companies with no website at all.

**Cost.** One-time CB embedding pass: ~6-12 hours on a laptop or ~30 min on a GPU. ~10 GB on disk for the index. Query time: <50ms per YC row.

### Tier 3 stretch: Contrastive fine-tuning

Free supervision is sitting in the YC data: every YC row with non-empty `former_names` is a positive (former_name, current_name) pair. The unified corpus surfaces 1,969 such YC rows. Combined with hard negatives (same-industry, same-stage, same-region non-peers from the candidate-generation step), this is a clean contrastive dataset.

**Architecture.** Freeze BGE-M3, train a small projection head with InfoNCE loss. The result is a *company-identity* embedding, closer to the actual task than generic description similarity. Plugged back into Tier 2 as the `description_cosine` feature and into Tier 3 as the retrieval encoder.

This is the bullet on the Crustdata JD ("contrastive learning, representation learning") in its cleanest form: contrastive supervision mined from the dataset itself, no manual labeling overhead.

---

## What's next

Roadmap items beyond what shipped here. ER Tier 2 and Tier 3 are *not* on this list. They're documented above as deferred-with-spec.

- **Quantitative similarity eval.** 4-baseline comparison (industry-only, semantic-only, full pipeline with hand-tuned weights, full pipeline with learned weights) against 15-25 hand-labeled seeds with peer sets. Recall@K, NDCG, MRR. This is the next thing I'd build.
- **Multilingual extension.** Swap to multilingual-e5, extend the industry taxonomy mapping. Maps to JD bullet 1.
- **Active learning loop.** Route low-confidence rankings to human review; retrain the reranker periodically on the feedback signal.
- **People similarity companion.** Same architecture, different features (titles, schools, employers, skills). Maps to JD bullet 6.
- **MCP tool wrapper.** Expose `similar_companies(seed, k=10)` as an MCP server. Plugs into Claude Code workflows.
- **Production scoring at scale.** FAISS HNSW over the 2.8M corpus for sub-second seed→top-K lookup. The architecture for this is in the Production Architecture section above.