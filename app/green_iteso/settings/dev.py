"""Disposable local development settings."""

# Settings modules intentionally re-export the shared base namespace.
# pylint: disable=invalid-name,wildcard-import,unused-wildcard-import

from .base import *  # noqa: F403
from .base import DEPLOYED as _DEPLOYED

if _DEPLOYED:
    DEBUG = False
else:
    DEBUG = True
