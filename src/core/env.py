"""Load environment variables from a project-root ``.env`` file.

Zero-dependency: uses python-dotenv when installed, otherwise a tiny built-in
parser — so secrets in ``.env`` work whether or not the optional dep is present.
Never overrides variables already set in the real environment (so an explicit
``GEMINI_API_KEY=... python -m ...`` still wins). ``.env`` is gitignored.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path | str | None = None) -> None:
    p = Path(path) if path else _ENV_PATH

    # Prefer python-dotenv when available.
    try:
        from dotenv import load_dotenv

        load_dotenv(p, override=False)
        return
    except Exception:
        pass

    # Fallback parser — KEY=VALUE lines, # comments, optional quotes.
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)
