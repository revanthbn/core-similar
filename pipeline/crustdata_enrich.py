"""
Step 2: Crustdata enrichment for the seed company.

Calls GET /screener/company once per seed with the rich fields list,
caches the response to disk, and exposes the result as a structured dict
that the criterion extraction prompt can consume directly.

Falls back to None if no CRUSTDATA_TOKEN is set or the API returns no
match — the pipeline downstream handles the missing-enrichment case
gracefully (criteria are extracted from local description only).

Environment:
    CRUSTDATA_TOKEN — required for actual API calls; if unset, returns None

Cache layout:
    data/cache/crustdata_enrichments/{domain}.json
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from pipeline.env import load_env  # auto-loads .env on import
load_env()

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache" / "crustdata_enrichments"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

API_BASE = "https://api.crustdata.com"
ENDPOINT = f"{API_BASE}/screener/company"
FIELDS = "headcount,founders.profiles,funding_and_investment,competitors,taxonomy,decision_makers,markets"
DEFAULT_TIMEOUT = 30
TOKEN_ENV = "CRUSTDATA_TOKEN"


@dataclass
class CrustdataEnrichment:
    """Selected, normalized fields lifted out of the Crustdata response."""
    domain: str
    matched: bool
    competitors: list[dict]        # [{name, domain, ...}]
    taxonomy: dict                  # category/industry breakdown
    founders: list[dict]            # [{name, background, ...}]
    decision_makers: list[dict]
    headcount: dict | None
    markets: list[str]
    raw: dict                       # original response

    def competitors_summary(self) -> str:
        """One-line list of competitor names for the criterion prompt."""
        names = []
        for c in self.competitors or []:
            n = c.get("name") or c.get("company_name")
            if n:
                names.append(n)
        return ", ".join(names[:15])

    def taxonomy_summary(self) -> str:
        if not self.taxonomy:
            return ""
        if isinstance(self.taxonomy, dict):
            # Common Crustdata shape: {industries: [...], sub_industries: [...], categories: [...]}
            parts = []
            for k, v in self.taxonomy.items():
                if isinstance(v, list) and v:
                    parts.append(f"{k}: {', '.join(str(x) for x in v[:6])}")
            return " | ".join(parts)
        return str(self.taxonomy)[:300]

    def founders_summary(self) -> str:
        out = []
        for f in self.founders or []:
            name = f.get("name") or ""
            bg = f.get("background") or f.get("title") or ""
            prev = f.get("previous_company") or f.get("previous_companies") or ""
            if isinstance(prev, list):
                prev = ", ".join(str(x) for x in prev[:3])
            out.append(f"{name} ({bg}; previously: {prev})".strip())
        return "; ".join(out[:6])


def _cache_path(domain: str) -> Path:
    safe = domain.replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _load_cached(domain: str) -> dict | None:
    p = _cache_path(domain)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _save_cached(domain: str, data: dict):
    _cache_path(domain).write_text(json.dumps(data, indent=2))


def _api_call(domain: str, exact_match: bool) -> dict | None:
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return None
    params = {
        "company_domain": domain,
        "fields": FIELDS,
        "exact_match": "true" if exact_match else "false",
    }
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(ENDPOINT, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        print(f"  [crustdata] request error for {domain}: {e}")
        return None
    if resp.status_code != 200:
        print(f"  [crustdata] {resp.status_code} for {domain}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


def _flatten_response(raw, seed_domain: str) -> CrustdataEnrichment:
    """Pull commonly-shaped fields out of the response, defensively.

    Crustdata's /screener/company returns a list of company dicts. Each
    dict has the actual schema fields:
        company_website_domain : "harvey.ai"
        domains                : ["harvey.ai"]
        company_website        : "http://harvey.ai"     (with scheme)
        is_full_domain_match   : bool
        taxonomy               : dict with linkedin_industries etc.
        competitors            : dict with competitor_website_domains list
        founders               : dict with profiles list
        headcount              : dict with linkedin_headcount fields
        decision_makers        : list of person dicts
        markets                : list of strings
    """
    body = raw
    if isinstance(raw, list):
        body = raw[0] if raw else {}
    elif isinstance(raw, dict) and "results" in raw and isinstance(raw["results"], list):
        body = raw["results"][0] if raw["results"] else {}
    if not isinstance(body, dict):
        body = {}

    # competitors: dict -> flatten to a list of {name, domain} from the
    # three competitor source lists Crustdata exposes
    raw_comp = body.get("competitors") or {}
    competitors = []
    if isinstance(raw_comp, dict):
        for key in ("competitor_website_domains",
                    "organic_seo_competitors_website_domains",
                    "paid_seo_competitors_website_domains"):
            for d in raw_comp.get(key) or []:
                if isinstance(d, str):
                    name = d.split(".")[0].replace("-", " ").title()
                    competitors.append({"name": name, "domain": d, "source": key})
                elif isinstance(d, dict):
                    competitors.append(d)
    elif isinstance(raw_comp, list):
        competitors = raw_comp
    # de-dup by domain
    seen = set()
    deduped = []
    for c in competitors:
        d = c.get("domain")
        if d and d not in seen:
            seen.add(d)
            deduped.append(c)
    competitors = deduped

    # founders.profiles
    raw_founders = body.get("founders") or {}
    if isinstance(raw_founders, dict):
        founders = raw_founders.get("profiles") or []
    elif isinstance(raw_founders, list):
        founders = raw_founders
    else:
        founders = []

    decision_makers = body.get("decision_makers") or []
    headcount = body.get("headcount") if isinstance(body.get("headcount"), dict) else None
    taxonomy = body.get("taxonomy") or {}
    markets = body.get("markets") or []

    # matched: prefer the explicit flag Crustdata returns, else compare host
    if "is_full_domain_match" in body:
        matched = bool(body["is_full_domain_match"])
    else:
        # Compare the seed_domain against company_website_domain or first of domains
        cb_host = body.get("company_website_domain")
        if not cb_host and isinstance(body.get("domains"), list) and body["domains"]:
            cb_host = body["domains"][0]
        matched = bool(cb_host) and str(cb_host).lower() == seed_domain.lower()

    return CrustdataEnrichment(
        domain=seed_domain,
        matched=matched,
        competitors=competitors,
        taxonomy=taxonomy if isinstance(taxonomy, dict) else {},
        founders=founders if isinstance(founders, list) else [],
        decision_makers=decision_makers if isinstance(decision_makers, list) else [],
        headcount=headcount,
        markets=markets if isinstance(markets, list) else [],
        raw=raw,
    )


def enrich(domain: str | None) -> CrustdataEnrichment | None:
    """Return a CrustdataEnrichment, using cache when available.

    Returns None if domain is None, or if both exact and fuzzy fetches
    fail (no token, no match, or API error).
    """
    if not domain:
        return None
    domain = domain.lower().lstrip("www.").rstrip("/")

    # 1) Cache
    cached = _load_cached(domain)
    if cached is not None:
        return _flatten_response(cached, domain)

    # 2) Live API
    token = os.environ.get(TOKEN_ENV)
    if not token:
        print(f"  [crustdata] CRUSTDATA_TOKEN not set; skipping enrichment for {domain}")
        return None

    print(f"  [crustdata] fetching {domain} (exact_match=true)")
    raw = _api_call(domain, exact_match=True)
    # Treat empty list / empty results as a miss
    empty = (
        raw is None
        or (isinstance(raw, list) and not raw)
        or (isinstance(raw, dict) and not raw.get("results") and not raw.get("name"))
    )
    if empty:
        time.sleep(0.5)
        print(f"  [crustdata]   empty; retrying exact_match=false")
        raw = _api_call(domain, exact_match=False)

    if not raw:
        return None

    # Save raw BEFORE parsing so a parse error leaves us debugging material
    _save_cached(domain, raw)
    return _flatten_response(raw, domain)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m pipeline.crustdata_enrich <domain>")
        sys.exit(1)
    e = enrich(sys.argv[1])
    if e is None:
        print("No enrichment available.")
        sys.exit(0)
    print(f"matched: {e.matched}")
    print(f"competitors ({len(e.competitors)}): {e.competitors_summary()}")
    print(f"taxonomy: {e.taxonomy_summary()}")
    print(f"founders: {e.founders_summary()}")
    print(f"headcount: {e.headcount}")
    print(f"markets: {e.markets}")
