"""
Step 5: Lazy embedding retrieval via OpenAI text-embedding-3-small.

For each candidate record_id from the prefilter:
  - If embedding cached on disk: use it.
  - Else: fetch the row's combined description, embed via OpenAI API,
          cache.
Then computes cosine similarity between seed embedding and all candidate
embeddings (in numpy), returns top-K.

Cache layout:
    data/cache/embeddings/embeddings.npz   — single compressed npz with two
                                              parallel arrays:
                                                ids    (object array of record_id)
                                                vecs   (float32, n × 1536)
    data/cache/embeddings/lookup.json      — {record_id: index_into_vecs}

Append-only: on each run we load existing, embed only the missing ones,
then atomically rewrite the npz.

Cost (text-embedding-3-small, $0.02/1M tokens):
    ~80-100 tokens per company × 30K candidates ≈ $0.06 per seed (first
    time only; cached thereafter).
"""

import json
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from pipeline.env import load_env
load_env()
from pipeline.seed_lookup import _load_index, fetch_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache" / "embeddings"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
NPZ_PATH = CACHE_DIR / "embeddings.npz"
LOOKUP_PATH = CACHE_DIR / "lookup.json"

MODEL = "text-embedding-3-small"
DIM = 1536
BATCH_SIZE = 256                # OpenAI accepts up to 2048; 256 keeps memory reasonable
MAX_TOKENS_PER_TEXT = 8000      # text-embedding-3-small limit ~8191

# Module-level cache to avoid re-reading the npz across calls in same process
_EMB_CACHE: dict = {"loaded": False}


@dataclass
class EmbedRetrievalResult:
    seed_id: str
    seed_emb: np.ndarray
    candidate_ids: list[str]    # ordered, parallel to scores
    scores: np.ndarray          # cosine similarity to seed, sorted desc
    top_k_ids: list[str]
    top_k_scores: list[float]
    n_new_embeddings: int
    elapsed_seconds: float


def _load_cache():
    if _EMB_CACHE.get("loaded"):
        return _EMB_CACHE
    if NPZ_PATH.exists() and LOOKUP_PATH.exists():
        try:
            data = np.load(NPZ_PATH, allow_pickle=False)
            vecs = data["vecs"].astype(np.float32, copy=False)
            lookup = json.loads(LOOKUP_PATH.read_text())
            _EMB_CACHE["vecs"] = vecs
            _EMB_CACHE["lookup"] = lookup
        except (zipfile.BadZipFile, ValueError, KeyError, EOFError) as e:
            # A prior process was killed mid-save and left a corrupted npz.
            # Treat the cache as empty so the next embed pass rebuilds cleanly.
            print(f"  [embed] cache corrupt ({type(e).__name__}); rebuilding from scratch")
            _EMB_CACHE["vecs"] = np.zeros((0, DIM), dtype=np.float32)
            _EMB_CACHE["lookup"] = {}
    else:
        _EMB_CACHE["vecs"] = np.zeros((0, DIM), dtype=np.float32)
        _EMB_CACHE["lookup"] = {}
    _EMB_CACHE["loaded"] = True
    return _EMB_CACHE


def _save_cache():
    """Persist the embedding cache.

    Tries an atomic write-and-rename first (so a mid-write kill leaves
    the prior cache intact). If the rename keeps failing on this
    filesystem we fall back to writing the npz in place. On the rare
    case where both write attempts leave the npz corrupted, `_load_cache`
    will detect the BadZipFile on next start and rebuild from scratch.
    """
    cache = _EMB_CACHE

    # ---- atomic try ----
    tmp_npz = NPZ_PATH.with_name(NPZ_PATH.name + ".tmp.npz")
    atomic_ok = False
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if tmp_npz.exists():
                tmp_npz.unlink()
            np.savez_compressed(tmp_npz, vecs=cache["vecs"])
            if not tmp_npz.exists():
                raise FileNotFoundError(f"numpy did not produce {tmp_npz}")
            os.replace(tmp_npz, NPZ_PATH)
            atomic_ok = True
            break
        except (OSError, FileNotFoundError) as e:
            last_err = e
            time.sleep(0.2 * (attempt + 1))

    if not atomic_ok:
        # Fallback: write npz in place. Non-atomic — a kill here would
        # leave a partial npz, which _load_cache handles by treating it
        # as an empty cache.
        print(f"  [embed] atomic save failed ({last_err}); writing in place")
        try:
            if tmp_npz.exists():
                tmp_npz.unlink()
        except OSError:
            pass
        np.savez_compressed(NPZ_PATH, vecs=cache["vecs"])

    # lookup.json is small; atomic rename is reliable here.
    tmp_lookup = LOOKUP_PATH.with_name(LOOKUP_PATH.name + ".tmp")
    tmp_lookup.write_text(json.dumps(cache["lookup"]))
    os.replace(tmp_lookup, LOOKUP_PATH)


def _build_text(row: dict) -> str:
    """Combined description for embedding."""
    parts = []
    for k in ("long_description", "one_liner"):
        v = row.get(k)
        if v:
            parts.append(v)
    if not parts:
        # Fallback to name + industries so we never embed an empty string
        name = row.get("name") or ""
        inds = ", ".join(row.get("industries", [])[:4])
        parts.append(f"{name} — {inds}".strip(" —"))
    text = "\n".join(parts)
    # OpenAI counts in tokens; we'll cap at ~6000 chars (~1500 tokens) to be safe
    return text[:6000]


def _embed_batch(texts: list[str], model: str = MODEL) -> np.ndarray:
    """Single OpenAI embedding API call. Returns float32 array (n, DIM)."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set; cannot embed via text-embedding-3-small"
        )
    from openai import OpenAI
    client = OpenAI()
    resp = client.embeddings.create(model=model, input=texts)
    out = np.array([d.embedding for d in resp.data], dtype=np.float32)
    return out


def embed_records(record_ids: Iterable[str], verbose: bool = True) -> dict[str, int]:
    """Ensure every record_id has a cached embedding. Returns the lookup map."""
    cache = _load_cache()
    lookup = cache["lookup"]
    vecs = cache["vecs"]

    missing = [rid for rid in record_ids if rid not in lookup]
    if not missing:
        return lookup

    # Fetch row text in offset order for sequential disk reads
    rows = fetch_rows(missing)
    new_ids = []
    new_texts = []
    for rid in missing:
        row = rows.get(rid)
        if row is None:
            continue
        text = _build_text(row)
        if not text:
            continue
        new_ids.append(rid)
        new_texts.append(text)

    if not new_ids:
        return lookup

    if verbose:
        print(f"  [embed] embedding {len(new_ids):,} new records via {MODEL}")

    n_batches = (len(new_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    t0 = time.time()
    SAVE_EVERY_N_BATCHES = 20  # ~5K embeddings between saves; bounded loss on kill

    for i in range(0, len(new_ids), BATCH_SIZE):
        batch_texts = new_texts[i:i + BATCH_SIZE]
        batch_ids   = new_ids[i:i + BATCH_SIZE]
        try:
            v = _embed_batch(batch_texts)
        except Exception as e:
            print(f"  [embed] batch {i // BATCH_SIZE + 1}/{n_batches} failed: {e}; retrying once")
            time.sleep(2)
            v = _embed_batch(batch_texts)

        # Append this batch to the cache in memory
        base = cache["vecs"].shape[0]
        if base:
            cache["vecs"] = np.vstack([cache["vecs"], v])
        else:
            cache["vecs"] = v
        for j, rid in enumerate(batch_ids):
            lookup[rid] = base + j

        batch_num = i // BATCH_SIZE + 1
        if batch_num % SAVE_EVERY_N_BATCHES == 0 or batch_num == n_batches:
            _save_cache()  # checkpoint to disk
            if verbose:
                elapsed = time.time() - t0
                rate = (i + BATCH_SIZE) / elapsed if elapsed > 0 else 0
                print(f"    batch {batch_num}/{n_batches} ({rate:,.0f} embeds/s)  ✓ checkpointed")
        elif verbose and batch_num % 5 == 0:
            elapsed = time.time() - t0
            rate = (i + BATCH_SIZE) / elapsed if elapsed > 0 else 0
            print(f"    batch {batch_num}/{n_batches} ({rate:,.0f} embeds/s)")
    if verbose:
        print(f"  [embed] saved {len(new_ids):,} new embeddings in {time.time() - t0:.0f}s")
    return lookup


def embed_seed_text(text: str) -> np.ndarray:
    """Embed a single text (the seed's combined_description)."""
    v = _embed_batch([text])
    return v[0]


def retrieve_top_k(seed_emb: np.ndarray, candidate_ids: list[str], k: int = 500) -> EmbedRetrievalResult:
    """Compute cosine similarity and return top-K candidates."""
    t0 = time.time()
    cache = _load_cache()
    lookup = cache["lookup"]
    vecs = cache["vecs"]

    # Filter to candidates we actually have embeddings for
    have_ids = [rid for rid in candidate_ids if rid in lookup]
    if not have_ids:
        return EmbedRetrievalResult(
            seed_id="", seed_emb=seed_emb, candidate_ids=[],
            scores=np.array([]), top_k_ids=[], top_k_scores=[],
            n_new_embeddings=0, elapsed_seconds=0.0,
        )
    idxs = np.fromiter((lookup[rid] for rid in have_ids), dtype=np.int64, count=len(have_ids))
    cand_vecs = vecs[idxs]

    # Cosine = dot product on already-normalized vectors. OpenAI embeddings
    # are NOT pre-normalized; normalize both sides.
    seed_n = seed_emb / (np.linalg.norm(seed_emb) + 1e-12)
    cand_norms = np.linalg.norm(cand_vecs, axis=1, keepdims=True) + 1e-12
    cand_n = cand_vecs / cand_norms

    scores = cand_n @ seed_n  # shape (n,)
    k = min(k, len(have_ids))
    if k <= 0:
        top_idx = np.array([], dtype=np.int64)
    else:
        # argpartition for speed; then sort top-k descending
        top_idx = np.argpartition(-scores, k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
    top_k_ids = [have_ids[i] for i in top_idx]
    top_k_scores = scores[top_idx].tolist()

    return EmbedRetrievalResult(
        seed_id="",
        seed_emb=seed_emb,
        candidate_ids=have_ids,
        scores=scores,
        top_k_ids=top_k_ids,
        top_k_scores=top_k_scores,
        n_new_embeddings=0,  # filled by caller
        elapsed_seconds=time.time() - t0,
    )


def embed_and_retrieve(seed, candidate_ids: list[str], k: int = 500, verbose: bool = True) -> EmbedRetrievalResult:
    """Convenience wrapper: ensure candidates embedded, embed seed,
    compute top-K. Seed embedding is NOT cached (cheap, and seed text
    can vary across enrichment versions)."""
    t0 = time.time()
    pre_n = len(_load_cache()["lookup"])
    embed_records(candidate_ids, verbose=verbose)
    post_n = len(_load_cache()["lookup"])

    seed_text = seed.combined_description() or seed.one_liner or seed.name
    seed_emb = embed_seed_text(seed_text)

    result = retrieve_top_k(seed_emb, candidate_ids, k=k)
    result.seed_id = seed.record_id
    result.n_new_embeddings = post_n - pre_n
    result.elapsed_seconds = time.time() - t0
    return result


if __name__ == "__main__":
    import sys
    from pipeline.seed_lookup import lookup_seed
    from pipeline.prefilter import prefilter
    if len(sys.argv) < 2:
        print("usage: python -m pipeline.embedding <name_or_domain> [k]")
        sys.exit(1)
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    seed = lookup_seed(sys.argv[1])
    print(f"Seed: {seed.name} ({seed.record_id})")
    pf = prefilter(seed)
    print(f"Prefilter: {pf.n_final:,} candidates")
    res = embed_and_retrieve(seed, pf.candidate_ids, k=k)
    idx = _load_index()
    print(f"\nTop {k} via cosine ({res.elapsed_seconds:.1f}s; {res.n_new_embeddings:,} new embeds):")
    for rid, score in zip(res.top_k_ids, res.top_k_scores):
        meta = idx["compact"].get(rid, {})
        name = meta.get("name", "?")
        dom = meta.get("domain") or "—"
        print(f"  {score:.3f}  {name[:40]:40s}  {dom}  ({rid[:30]})")
