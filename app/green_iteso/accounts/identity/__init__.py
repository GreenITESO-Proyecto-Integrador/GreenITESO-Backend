"""Identity providers for institutional login, selected by settings."""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import ExternalIdentity, IdentityProvider
from .entra import EntraProvider
from .mock import MockProvider

__all__ = ["ExternalIdentity", "IdentityProvider", "get_identity_provider"]


@lru_cache(maxsize=4)
def _entra_provider(tenant_id: str, client_id: str) -> EntraProvider:
    # Cached so the JWKS keys are not refetched on every login.
    return EntraProvider(tenant_id=tenant_id, client_id=client_id)


def get_identity_provider() -> IdentityProvider:
    """Return the provider configured by ``MICROSOFT_AUTH_MODE``."""
    if settings.MICROSOFT_AUTH_MODE == "mock":
        return MockProvider()
    if not settings.MICROSOFT_TENANT_ID or not settings.MICROSOFT_CLIENT_ID:
        raise ImproperlyConfigured(
            "MICROSOFT_TENANT_ID and MICROSOFT_CLIENT_ID are required for Entra login."
        )
    return _entra_provider(settings.MICROSOFT_TENANT_ID, settings.MICROSOFT_CLIENT_ID)
