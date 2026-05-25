"""
Step 3: Criterion extraction via GPT-4o.

One LLM call per seed. Takes the merged seed record + optional Crustdata
enrichment, produces 4-6 atomic, independently-verifiable criteria as
structured JSON.

Public API:
    extract_criteria(seed: SeedRecord, enrichment: CrustdataEnrichment | None,
                     model: str = "gpt-4o") -> list[Criterion]

Cache: per (seed.record_id, criteria_prompt_version) at
    data/cache/criteria/{seed.record_id}.json
"""

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from pipeline.env import load_env
load_env()

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache" / "criteria"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = "gpt-5-mini-2025-08-07"
PROMPT_VERSION = "v3"

SYSTEM_PROMPT = """You are an analyst building a similarity model for B2B sales and VC sourcing.
You extract atomic, verifiable criteria that distinguish a company's true peers from companies that merely share an industry tag.

Each criterion must be:
- A single proposition (NOT a compound statement; split AND/OR clauses)
- Verifiable from a candidate company's description alone (NOT from headcount or funding stage — those are hard filters applied separately)
- Specific enough to differentiate peers from non-peers (avoid generic statements like "is in technology")
- Worded as a third-person statement, not a question

When the user query specifies a niche, geography, or differentiator (e.g.
"European BNPL", "ML infrastructure not foundation labs", "for technical
teams"), ENCODE that intent as one or more criteria. The user's intent
takes precedence over what the seed's bare description implies.

Output STRICTLY as JSON. No prose, no markdown."""


USER_TEMPLATE = """User query: "{user_query}"

Seed company (the company the user named in their query):
Name: {name}
Description:
{description}

Industries (from local data): {industries}

{enrichment_block}

Produce 4-6 criteria that a TRUE PEER would satisfy, taking BOTH the
seed's nature AND the user's stated intent into account.

If the user query mentions a specific niche/vertical/geography/product
that narrows the seed's broader category (e.g. "European" or "BNPL"
within a broader fintech seed), include that as an explicit criterion —
do NOT drop it just because the seed itself is broader.

Cover dimensions like: industry/domain, product/service category, who
they serve, business model, technical approach, and notable
differentiators or constraints from the user query.

You MAY include criteria about geography ONLY when the user's query
explicitly mentions it (e.g. "European"). Otherwise, do not include
geographic criteria — headquarters location is filtered separately.

Do NOT include criteria about company size, headcount, founded year, or
funding stage.

Output strictly as JSON with this schema:
{{
  "criteria": [
    {{"id": 1, "text": "<the criterion>", "rationale": "<why this separates peers from non-peers>"}},
    {{"id": 2, ...}}
  ]
}}
"""


@dataclass
class Criterion:
    id: int
    text: str
    rationale: str


def _enrichment_block(enrichment) -> str:
    """Format the Crustdata enrichment for the prompt, or empty string if none."""
    if enrichment is None or not getattr(enrichment, "matched", False):
        return "(No external enrichment available; rely on the local description above.)"
    parts = ["Crustdata enrichment for richer context:"]
    if enrichment.competitors:
        parts.append(f"- Competitors (per Crustdata, useful as 'what peers look like'): {enrichment.competitors_summary()}")
    if enrichment.taxonomy:
        parts.append(f"- Taxonomy: {enrichment.taxonomy_summary()}")
    if enrichment.founders:
        parts.append(f"- Founder backgrounds: {enrichment.founders_summary()}")
    if enrichment.markets:
        parts.append(f"- Markets: {', '.join(str(m) for m in enrichment.markets[:6])}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _cache_path(seed_record_id: str, user_query: str | None = None) -> Path:
    safe = seed_record_id.replace(":", "_").replace("/", "_")
    if user_query:
        qhash = hashlib.sha256(user_query.encode()).hexdigest()[:8]
        return CACHE_DIR / f"{safe}__{qhash}.json"
    return CACHE_DIR / f"{safe}.json"


def _build_prompt(seed, enrichment, user_query: str | None) -> tuple[str, str]:
    desc = seed.combined_description() or seed.one_liner or "(no description available)"
    industries = ", ".join(seed.industries[:8]) if seed.industries else "(none)"
    user = USER_TEMPLATE.format(
        user_query=user_query or f"companies similar to {seed.name}",
        name=seed.name,
        description=desc[:2500],
        industries=industries,
        enrichment_block=_enrichment_block(enrichment),
    )
    return SYSTEM_PROMPT, user


def _prompt_fingerprint(system: str, user: str) -> str:
    """Hash of system+user used as a cache key alongside seed.record_id, so
    cache invalidates automatically when prompt text changes."""
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(system.encode())
    h.update(user.encode())
    return h.hexdigest()[:16]


def extract_criteria(seed, enrichment=None, user_query: str | None = None,
                     model: str = DEFAULT_MODEL,
                     use_cache: bool = True) -> list[Criterion]:
    """Single LLM call producing 4-6 atomic criteria for the (seed, user_query) pair.

    The user_query is what differentiates "companies similar to Klarna" from
    "European BNPL companies like Klarna" — both have the same seed, but the
    second produces tighter criteria.
    """
    system, user = _build_prompt(seed, enrichment, user_query)
    fingerprint = _prompt_fingerprint(system, user)

    cache_path = _cache_path(seed.record_id, user_query)
    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("fingerprint") == fingerprint:
                return [Criterion(**c) for c in cached["criteria"]]
        except (json.JSONDecodeError, KeyError):
            pass

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set; cannot call GPT-4o for criterion extraction"
        )

    # Lazy import so the module loads without openai installed in env-check phase
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        response_format={"type": "json_object"},
    )
    raw_content = resp.choices[0].message.content
    parsed = json.loads(raw_content)
    items = parsed.get("criteria", [])
    criteria = [Criterion(id=c["id"], text=c["text"], rationale=c.get("rationale", "")) for c in items]

    # Cache
    cache_path.write_text(json.dumps({
        "seed_record_id": seed.record_id,
        "seed_name":      seed.name,
        "user_query":     user_query,
        "fingerprint":    fingerprint,
        "model":          model,
        "criteria":       [asdict(c) for c in criteria],
    }, indent=2))

    return criteria


if __name__ == "__main__":
    import sys
    from pipeline.seed_lookup import lookup_seed
    from pipeline.crustdata_enrich import enrich

    if len(sys.argv) < 2:
        print("usage: python -m pipeline.criteria <name_or_domain>")
        sys.exit(1)
    seed = lookup_seed(sys.argv[1])
    print(f"Seed: {seed.name} ({seed.record_id})")
    enrichment = enrich(seed.domain)
    print(f"Enrichment matched: {bool(enrichment and enrichment.matched)}")
    print(f"\nExtracting criteria via {DEFAULT_MODEL}...")
    crits = extract_criteria(seed, enrichment)
    for c in crits:
        print(f"\n[{c.id}] {c.text}")
        print(f"    rationale: {c.rationale}")
