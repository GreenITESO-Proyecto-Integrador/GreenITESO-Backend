"""Production settings with explicit cloud connection requirements."""

# Settings modules intentionally re-export the shared base namespace.
# pylint: disable=wildcard-import,unused-wildcard-import

from .base import *  # noqa: F403,F401
from .base import DEPLOYED as _DEPLOYED

if not _DEPLOYED:
    raise RuntimeError("DJANGO_ENV=production requires DJANGO_DEPLOYED=true.")
