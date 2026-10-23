"""Throttles for the unauthenticated auth endpoints (#186).

DRF's default throttles for anonymous requests key on the client IP. On a
campus network many users share one public IP, so a per-IP login or refresh
limit blocks a whole classroom while barely slowing down an attacker who
rotates ``X-Forwarded-For``. These throttles key on the identity named in
the request body instead, and a separate, much higher per-IP ceiling still
caps floods.

The identity is read **without verifying** the token: it only picks a rate
bucket. A forged claim just moves the caller to another bucket, which the
per-IP ceiling still covers; the view verifies the token afterwards.
"""

from __future__ import annotations

import hashlib
from typing import Any

import jwt
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


def _unverified_claim(token: str, claim: str) -> str:
    """Return ``claim`` from a JWT payload without checking its signature."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return ""
    value = payload.get(claim)
    return str(value) if value else ""


class _BodyIdentityThrottle(ScopedRateThrottle):
    """Scoped throttle keyed on an identity taken from the request body.

    ``token_field`` names the body field and ``claim`` the JWT claim that
    identifies the person. A token that is not a JWT is keyed on its hash, so
    retrying the same value is still limited; without a token the key falls
    back to the client IP.
    """

    token_field = ""
    claim = ""

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        data = request.data if isinstance(request.data, dict) else {}
        token = data.get(self.token_field)
        if isinstance(token, str) and token:
            subject = _unverified_claim(token, self.claim)
            ident = (
                f"{self.claim}:{subject}"
                if subject
                else "token:" + hashlib.sha256(token.encode()).hexdigest()
            )
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginIdentityThrottle(_BodyIdentityThrottle):
    """Limit logins per Microsoft account (``oid`` of the ID token)."""

    token_field = "id_token"
    claim = "oid"


class RefreshTokenIdentityThrottle(_BodyIdentityThrottle):
    """Limit refresh/logout calls per user (``user_id`` of the refresh token)."""

    token_field = "refresh"
    claim = "user_id"


class AuthIPCeilingThrottle(SimpleRateThrottle):
    """High per-IP ceiling shared by the auth endpoints to cap floods.

    Set ``DJANGO_NUM_PROXIES`` so DRF reads the real client IP from
    ``X-Forwarded-For``; otherwise the whole header is the key and a caller
    can change it at will.
    """

    scope = "auth_ip"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }
