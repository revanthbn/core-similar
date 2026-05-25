"""
Step 6: Single LLM rerank call on the top-K embedding candidates.

This module replaces an earlier per-candidate batched verifier with one
holistic OpenAI call that:
  - receives the seed, the extracted criteria, and the top-K candidates
    (with their cosine scores),
  - returns per-candidate per-criterion match decisions + evidence,
    plus a holistic tier (tier_1..tier_4) and a one-line tier rationale.

Public API:
    rerank_candidates(seed, criteria, candidate_ids, *,
                      cosine_scores=None,
                      model="gpt-5-mini-2025-08-07",
                      verbose=True) -> RerankResult

    verify_candidates(seed, criteria, candidate_ids, ...)  # back-compat
        -> dict[record_id, list[CriterionMatch]]

The full prompt + parsed JSON response are logged to
data/cache/rerank_calls/{seed_slug}_{utc_iso}.json for debugging.
Per design there is no content-hash cache — every run hits the model.
At ~$0.01-0.05 per query this is intentional.
"""

import json
import os
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

from pipeline.env import load_env
load_env()
from pipeline.seed_lookup import fetch_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "data" / "cache" / "rerank_calls"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = "gpt-5-mini-2025-08-07"

VALID_TIERS = ("tier_1", "tier_2", "tier_3", "tier_4")

SYSTEM_PROMPT = """You are an expert B2B analyst ranking candidate companies by similarity to a seed company.
You will receive criteria, the seed company's profile, and up to 25 candidate companies that have already been retrieved by semantic embedding.
Your job is to re-rank these candidates and produce structured match evidence.

Output STRICTLY as JSON. No prose, no markdown."""


USER_TEMPLATE = """# CRITERIA
A true peer of this seed should satisfy these criteria:
{criteria_block}

# SEED COMPANY
Name: {seed_name}
Domain: {seed_domain}
Description:
{seed_description}

# CANDIDATES
The following {n_candidates} candidate(s) have been pre-retrieved by semantic similarity to the seed (cosine score in parentheses). For each candidate, decide independently for each criterion whether the candidate satisfies it.

{candidates_block}

# YOUR TASK
For each candidate:
1. For each criterion, decide MATCH (true) or NO_MATCH (false) based on the description.
2. When MATCH, provide a one-sentence evidence quote or paraphrase from the description.
3. Compute a holistic similarity tier:
   - tier_1: matches all or nearly all criteria, strong peer
   - tier_2: matches most criteria, plausible peer
   - tier_3: matches some criteria, adjacent
   - tier_4: matches only one criterion or is weakly related

After scoring all candidates, return a re-ranked list ordered by overall peer quality. Use cosine_score AND criterion satisfaction together: if a candidate has high cosine but misses critical criteria, rank it lower than a candidate with slightly lower cosine that matches more criteria.

When critical signals are missing from a description (e.g., size, geography), do NOT penalize harshly — note "insufficient evidence" in the evidence field rather than calling NO_MATCH purely on absence.

# OUTPUT FORMAT (strict JSON)
{{
  "ranked_candidates": [
    {{
      "candidate_id": "C01",
      "name": "...",
      "domain": "...",
      "cosine_score": 0.87,
      "criterion_matches": [
        {{"criterion_id": 1, "match": true, "evidence": "Their description explicitly mentions ..."}},
        {{"criterion_id": 2, "match": false, "evidence": "No mention of safety/alignment in the description."}}
      ],
      "tier": "tier_1",
      "tier_rationale": "Matches 4/4 criteria with strong evidence for ..."
    }}
  ]
}}

Return ONLY the JSON object, no preamble or commentary."""


@dataclass
class CriterionMatch:
    """Per-candidate per-criterion verdict. Same shape as the legacy verifier
    used so downstream rank.py + the UI's evidence rendering keep working."""
    criterion_id: int
    match: bool
    evidence: str = ""


@dataclass
class RerankResult:
    matches: dict[str, list[CriterionMatch]] = field(default_factory=dict)
    tiers: dict[str, str] = field(default_factory=dict)
    rationales: dict[str, str] = field(default_factory=dict)
    raw: dict | None = None
    model: str = DEFAULT_MODEL
    elapsed_seconds: float = 0.0


def _short_description(row: dict, limit: int = 600) -> str:
    """Combined short description for the prompt. Caps length so the full
    25-candidate block stays well under the context budget."""
    parts: list[str] = []
    for k in ("one_liner", "long_description"):
        v = row.get(k)
        if v:
            parts.append(v)
            break
    text = "\n".join(parts) if parts else (row.get("name") or "")
    return text[:limit]


def _slugify_for_filename(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", s).strip("_") or "seed"


def _build_prompt(seed, criteria, batch: list[tuple[str, str, dict]],
                  cosine_scores: dict[str, float] | None) -> tuple[str, str]:
    criteria_lines = "\n".join(
        f"  {c.id}. {c.text}" for c in criteria
    )

    seed_desc = ""
    try:
        seed_desc = seed.combined_description()
    except Exception:
        seed_desc = ""
    if not seed_desc:
        seed_desc = getattr(seed, "one_liner", "") or seed.name
    seed_desc = (seed_desc or "")[:2500]

    cand_lines = []
    for label, rid, row in batch:
        cosine = (cosine_scores or {}).get(rid)
        cosine_str = f"{cosine:.3f}" if cosine is not None else "n/a"
        name = row.get("name", "") or ""
        domain = row.get("domain") or "unknown"
        cand_lines.append(
            f"[{label}] (cosine={cosine_str})\n"
            f"  Name: {name}\n"
            f"  Domain: {domain}\n"
            f"  Description: {_short_description(row)}"
        )
    candidates_block = "\n\n".join(cand_lines) if cand_lines else "(no candidates)"

    user = USER_TEMPLATE.format(
        criteria_block=criteria_lines,
        seed_name=seed.name,
        seed_domain=getattr(seed, "domain", None) or "unknown",
        seed_description=seed_desc,
        n_candidates=len(batch),
        candidates_block=candidates_block,
    )
    return SYSTEM_PROMPT, user


def _llm_call(system: str, user: str, model: str) -> dict:
    """One OpenAI call. Returns the parsed JSON dict."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set; cannot call the rerank model"
        )
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    return json.loads(content)


def _log_call(seed, criteria, system: str, user: str, raw: dict,
              model: str, elapsed: float) -> Path:
    seed_slug = _slugify_for_filename(getattr(seed, "record_id", seed.name))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"{seed_slug}_{ts}.json"
    payload = {
        "seed": {
            "name": seed.name,
            "domain": getattr(seed, "domain", None),
            "record_id": getattr(seed, "record_id", None),
        },
        "model": model,
        "elapsed_seconds": round(elapsed, 2),
        "criteria": [
            {"id": c.id, "text": c.text, "rationale": getattr(c, "rationale", "")}
            for c in criteria
        ],
        "prompt": {"system": system, "user": user},
        "response": raw,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def _parse_response(
    raw: dict,
    batch: list[tuple[str, str, dict]],
    criteria,
) -> tuple[dict[str, list[CriterionMatch]], dict[str, str], dict[str, str]]:
    """Convert the LLM's ranked_candidates list into:
       (matches_by_rid, tier_by_rid, rationale_by_rid)
    """
    label_to_rid = {label: rid for label, rid, _row in batch}
    crit_ids = [c.id for c in criteria]

    matches: dict[str, list[CriterionMatch]] = {}
    tiers: dict[str, str] = {}
    rationales: dict[str, str] = {}

    for cand in raw.get("ranked_candidates", []) or []:
        label = cand.get("candidate_id") or cand.get("candidate")
        rid = label_to_rid.get(label)
        if not rid:
            continue

        # Per-criterion matches: collect whatever the LLM returned, then
        # backfill any omitted criteria with NO_MATCH so rank.py never
        # sees a hole in the criterion grid.
        cm_list: list[CriterionMatch] = []
        seen_cids: set[int] = set()
        for m in cand.get("criterion_matches", []) or []:
            try:
                cid = int(m["criterion_id"])
            except (KeyError, ValueError, TypeError):
                continue
            cm_list.append(CriterionMatch(
                criterion_id=cid,
                match=bool(m.get("match", False)),
                evidence=str(m.get("evidence") or "")[:400],
            ))
            seen_cids.add(cid)
        for cid in crit_ids:
            if cid not in seen_cids:
                cm_list.append(CriterionMatch(
                    criterion_id=cid, match=False, evidence=""
                ))
        cm_list.sort(key=lambda x: x.criterion_id)
        matches[rid] = cm_list

        tier_val = (cand.get("tier") or "").strip().lower()
        if tier_val not in VALID_TIERS:
            # Be permissive: accept "1".."4" or "tier1" etc.
            digit_match = re.search(r"[1-4]", tier_val)
            tier_val = f"tier_{digit_match.group(0)}" if digit_match else "tier_4"
        tiers[rid] = tier_val
        rationales[rid] = str(cand.get("tier_rationale") or "")[:400]

    return matches, tiers, rationales


def rerank_candidates(
    seed,
    criteria,
    candidate_ids: list[str],
    *,
    cosine_scores: dict[str, float] | None = None,
    model: str = DEFAULT_MODEL,
    verbose: bool = True,
) -> RerankResult:
    """Single LLM call that reranks candidate_ids holistically.

    See the module docstring for the JSON schema the model is asked to
    return; the parsed result is returned as a RerankResult with the same
    per-criterion match shape downstream consumers expect.
    """
    if not candidate_ids:
        return RerankResult(model=model)

    rows = fetch_rows(candidate_ids)

    batch: list[tuple[str, str, dict]] = []
    for i, rid in enumerate(candidate_ids):
        row = rows.get(rid)
        if row is None:
            continue
        label = f"C{i + 1:02d}"
        batch.append((label, rid, row))

    if not batch:
        return RerankResult(model=model)

    system, user = _build_prompt(seed, criteria, batch, cosine_scores)

    if verbose:
        print(f"  [rerank] one call: {len(batch)} candidates × {len(criteria)} criteria via {model}")

    t0 = time.time()
    try:
        raw = _llm_call(system, user, model)
    except Exception as e:
        if verbose:
            print(f"  [rerank] call failed: {e}; retrying once")
        time.sleep(2)
        raw = _llm_call(system, user, model)
    elapsed = time.time() - t0

    log_path = _log_call(seed, criteria, system, user, raw, model, elapsed)
    if verbose:
        print(f"  [rerank] done in {elapsed:.1f}s; logged to {log_path.relative_to(REPO_ROOT)}")

    matches, tiers, rationales = _parse_response(raw, batch, criteria)

    if verbose:
        tier_counts: dict[str, int] = {}
        for t in tiers.values():
            tier_counts[t] = tier_counts.get(t, 0) + 1
        breakdown = ", ".join(
            f"{t}:{tier_counts.get(t, 0)}" for t in VALID_TIERS
        )
        print(f"  [rerank] tier distribution → {breakdown}")

    return RerankResult(
        matches=matches,
        tiers=tiers,
        rationales=rationales,
        raw=raw,
        model=model,
        elapsed_seconds=elapsed,
    )


def verify_candidates(
    seed,
    criteria,
    candidate_ids: list[str],
    *,
    batch_size: int | None = None,
    model: str = DEFAULT_MODEL,
    verbose: bool = True,
    cosine_scores: dict[str, float] | None = None,
    **_ignored,
) -> dict[str, list[CriterionMatch]]:
    """Back-compat wrapper. Delegates to rerank_candidates and returns only
    the per-candidate match dict — the shape the previous batched verifier
    exposed. `batch_size` is accepted but ignored (one call total now)."""
    result = rerank_candidates(
        seed, criteria, candidate_ids,
        cosine_scores=cosine_scores,
        model=model,
        verbose=verbose,
    )
    return result.matches


# Optional asdict shim for ad-hoc dumping in scripts/notebooks.
def matches_to_jsonable(matches: dict[str, list[CriterionMatch]]) -> dict[str, list[dict]]:
    return {rid: [asdict(m) for m in ms] for rid, ms in matches.items()}
