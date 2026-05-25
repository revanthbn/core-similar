"""
Local UI host with a small LLM-powered query-mapping endpoint.

Run with:
    python -m scripts.serve_ui
then visit http://localhost:8000/

Why this exists: ui/index.html is purely static and only has results for the
seeds that have been pre-computed. When the user types a free-form query
("saas applications like harvey", "AI lawyer tools") the static UI cannot
guess which pre-computed seed they meant. This server adds a single
`/api/map_query?q=...` endpoint that asks gpt-5-mini to pick the best
match from the available slugs. The OpenAI key never leaves the host.

The endpoint returns JSON:
    {"slug": "harvey"}                — confident match
    {"slug": null, "reason": "..."}   — no plausible match
"""

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.env import load_env
load_env()

UI_DIR = REPO_ROOT / "ui"
RESULTS_DIR = REPO_ROOT / "data" / "results"

MAP_MODEL = os.environ.get("MAP_QUERY_MODEL", "gpt-5-mini-2025-08-07")

SYSTEM_PROMPT = """You match a user's free-form company-similarity query to one of a small set of pre-computed seed companies.
Return STRICT JSON: {"slug": "<one of the provided slugs>"} when the query plausibly maps to one of them, or {"slug": null} when no provided seed is a reasonable match.

A "reasonable match" means the seed company would be a sensible peer to surface for the query. Synonyms, paraphrases, and topic-level matches count (e.g. "legal AI", "AI lawyer tools", "saas applications like harvey" → harvey). Different verticals or unrelated industries do NOT count.

Use the seed name + the demo's example query for context. Pick the BEST match; do not pick "okay" matches when nothing fits well."""


def available_seeds() -> list[dict]:
    """Read the result JSONs to discover what seeds are available."""
    out: list[dict] = []
    if not RESULTS_DIR.exists():
        return out
    for p in sorted(RESULTS_DIR.glob("*_results.json")):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        slug = p.stem.replace("_results", "")
        out.append({
            "slug":       slug,
            "seed_name":  (d.get("seed") or {}).get("name") or slug,
            "domain":     (d.get("seed") or {}).get("domain") or "",
            "industries": ((d.get("seed") or {}).get("industries") or [])[:5],
            "example_query": d.get("user_query") or "",
        })
    return out


def map_query_via_llm(query: str, seeds: list[dict]) -> dict:
    """Single LLM call to pick the best matching slug."""
    if not seeds:
        return {"slug": None, "reason": "no pre-computed seeds available"}
    if not os.environ.get("OPENAI_API_KEY"):
        return {"slug": None, "reason": "OPENAI_API_KEY not set on the server"}

    from openai import OpenAI
    client = OpenAI()

    seeds_block = "\n".join(
        f"- slug={s['slug']}; seed_name={s['seed_name']}; "
        f"domain={s['domain']}; industries={s['industries']}; "
        f"example_query={s['example_query']!r}"
        for s in seeds
    )
    user = (
        f"Available seeds:\n{seeds_block}\n\n"
        f"User query: {query!r}\n\n"
        f"Return JSON: {{\"slug\": \"<one of the slugs above>\"}} or "
        f"{{\"slug\": null}} if nothing is a reasonable match."
    )

    try:
        resp = client.chat.completions.create(
            model=MAP_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"slug": None, "reason": f"LLM call failed: {e}"}

    slug = raw.get("slug") if isinstance(raw, dict) else None
    valid = {s["slug"] for s in seeds}
    if slug not in valid:
        return {"slug": None, "reason": f"model returned unknown slug {slug!r}"}
    return {"slug": slug, "reason": ""}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Quieter logs; default handler is too noisy for the demo.
        sys.stderr.write(f"  [serve] {self.address_string()} {fmt % args}\n")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/map_query":
            qs = parse_qs(parsed.query)
            query = (qs.get("q") or [""])[0].strip()
            if not query:
                self._json(400, {"slug": None, "reason": "missing q parameter"})
                return
            seeds = available_seeds()
            result = map_query_via_llm(query, seeds)
            self._json(200, result)
            return
        if parsed.path == "/api/seeds":
            self._json(200, {"seeds": available_seeds()})
            return
        # Default: serve static files out of UI_DIR
        super().do_GET()


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    if not UI_DIR.exists():
        print(f"ui dir missing: {UI_DIR}", file=sys.stderr)
        sys.exit(1)
    seeds = available_seeds()
    print(f"[serve] root: {UI_DIR}")
    print(f"[serve] {len(seeds)} pre-computed seeds: "
          f"{', '.join(s['slug'] for s in seeds)}")
    print(f"[serve] mapping model: {MAP_MODEL}")
    print(f"[serve] open http://{host}:{port}/")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    main(args.host, args.port)
