"""Select settings explicitly from ``DJANGO_ENV``."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT.parent / ".env", override=False)

_ENVIRONMENT = os.environ.get("DJANGO_ENV")
if not _ENVIRONMENT:
    raise RuntimeError(
        "DJANGO_ENV is required; set it to one of: dev, staging, production. "
        "Copy .env.example to .env for local development."
    )

if _ENVIRONMENT not in {"dev", "staging", "production"}:
    raise RuntimeError(
        f"Unsupported DJANGO_ENV={_ENVIRONMENT!r}; choose dev, staging, or production."
    )

if _ENVIRONMENT == "dev":
    from .dev import *  # noqa: F403
elif _ENVIRONMENT == "staging":
    from .staging import *  # noqa: F403
else:
    from .production import *  # noqa: F403
