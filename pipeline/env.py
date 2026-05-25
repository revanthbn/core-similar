"""
Load .env at the repo root into os.environ. Idempotent.

Imported by every pipeline module that touches API keys. Keys are read
via os.environ.get(...) at the call site — they never appear in source.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

_LOADED = False


def load_env(verbose: bool = False) -> None:
    """Load REPO_ROOT/.env into os.environ once per process."""
    global _LOADED
    if _LOADED:
        return
    if not ENV_PATH.exists():
        _LOADED = True
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        # Fallback parser if python-dotenv isn't installed
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
    _LOADED = True
    if verbose:
        for k in ("OPENAI_API_KEY", "CRUSTDATA_TOKEN"):
            if os.environ.get(k):
                # NEVER print the value
                print(f"  [env] {k} loaded ({len(os.environ[k])} chars)")


# Auto-load on import so any pipeline submodule's first import primes env
load_env()
