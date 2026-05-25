"""Shared text normalization helpers used by curation and the ER module."""

import re

CORPORATE_SUFFIXES = [
    ", inc.", ", inc", " inc.", " inc",
    ", llc.", ", llc", " llc.", " llc",
    ", ltd.", ", ltd", " ltd.", " ltd",
    " co.", " co", " corp.", " corp", " corporation",
    " s.p.a.", " spa", " s.r.l.", " srl",
    " gmbh", " ag", " ab", " bv", " sa", " sas",
    " pte. ltd.", " pte ltd",
]


def normalize_name(s):
    """Lowercase, strip common corporate suffixes, collapse whitespace/punct."""
    if s is None:
        return ""
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return ""
    s = s.lower()
    for suffix in CORPORATE_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_DOMAIN_RE = re.compile(r"https?://(?:www\.)?([^/?#]+)", re.IGNORECASE)


def extract_domain(url):
    """Pull just the host portion from a URL. Returns None on bad input."""
    if url is None:
        return None
    s = str(url).strip()
    if not s or s.lower() == "nan":
        return None
    m = _DOMAIN_RE.search(s)
    if m:
        return m.group(1).lower().strip()
    # No scheme: treat the string itself as a hostname if it looks like one
    if "." in s and " " not in s:
        host = s.lower().strip("/")
        # removeprefix (Python 3.9+) — strips substring, not chars.
        # lstrip("www.") would have eaten leading w's from e.g. "wiz.io".
        if host.startswith("www."):
            host = host[4:]
        return host
    return None
