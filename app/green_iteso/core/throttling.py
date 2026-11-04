"""Throttles for the unauthenticated auth endpoints (#186).

DRF's default throttles for anonymous requests key on the client IP. On a
campus network many users share one public IP, so a per-IP login or refresh
limit blocks a whole classroom. These throttles key on the person instead,
and a separate, much higher per-IP ceiling still caps floods.

A bucket must never be chosen from a claim the caller can forge into
someone else's identity, or a forged token could spend the victim's quota:

- refresh/logout verify the refresh token's signature (local, SimpleJWT)
  and fall back to the client IP when it is not valid;
- login cannot verify the Microsoft ID token here, so it keys on the pair
  (client IP, ``oid``): a forged ``oid`` only spends the caller's own bucket.

Unverified input is always hashed before it becomes part of a cache key, so
a huge claim cannot grow the cache.
"""

from __future__ import annotations

import hashlib
from typing import Any

import jwt
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

# Set on the request when the refresh token's signature checked out, so the
# shared per-IP ceiling can leave that request to its per-user bucket.
VERIFIED_REFRESH_ATTR = "_auth_throttle_verified_refresh"


def _body_token(request: Request, field: str) -> str:
    data = request.data if isinstance(request.data, dict) else {}
    token = data.get(field)
    return token if isinstance(token, str) else ""


class LoginIdentityThrottle(ScopedRateThrottle):
    """Limit logins per (client IP, Microsoft account).

    ``oid`` is read without verifying the token, which is why the IP is part
    of the key. It is hashed, so the key length is fixed whatever the caller
    sends. A token without an ``oid`` (or not a JWT) is keyed on its own hash,
    so retrying the same value is still limited.
    """

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        ip = self.get_ident(request)
        token = _body_token(request, "id_token")
        subject = ""
        if token:
            try:
                payload = jwt.decode(token, options={"verify_signature": False})
                oid = str(payload.get("oid") or "")
            except jwt.PyJWTError:
                oid = ""
            subject = hashlib.sha256((oid or token).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": f"{ip}|{subject}"}


class RefreshTokenIdentityThrottle(ScopedRateThrottle):
    """Limit refresh/logout per user, from a refresh token with a valid signature.

    An invalid, expired or forged token falls back to the client IP, so it can
    never spend another user's quota.
    """

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        token = _body_token(request, "refresh")
        ident = self.get_ident(request)
        if token:
            try:
                user_id = RefreshToken(token).get(jwt_settings.USER_ID_CLAIM)
            except TokenError:
                user_id = None
            if user_id:
                ident = f"user:{user_id}"
                setattr(request, VERIFIED_REFRESH_ATTR, True)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AuthIPCeilingThrottle(SimpleRateThrottle):
    """High per-IP ceiling shared by the auth endpoints to cap floods.

    A refresh/logout whose token signature checked out already has its own
    per-user bucket and skips the ceiling, so a flood of logins from a campus
    IP cannot make valid refreshes fail and log everyone out. Requires the
    per-user throttle to run first.

    Set ``DJANGO_NUM_PROXIES`` so DRF reads the real client IP from
    ``X-Forwarded-For``; otherwise the whole header is the key and a caller
    can change it at will.
    """

    scope = "auth_ip"

    def allow_request(self, request: Request, view: Any) -> bool:
        if getattr(request, VERIFIED_REFRESH_ATTR, False):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class FirstRejectionThrottleMixin:
    """Stop at the first throttle that rejects the request.

    DRF evaluates every throttle, so a request already rejected by the
    per-account limit would still be counted by the shared per-IP ceiling,
    letting one user's retries eat the quota of everyone behind the same NAT.
    List the per-account throttle first.
    """

    def check_throttles(self, request: Request) -> None:
        for throttle in self.get_throttles():  # type: ignore[attr-defined]
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())  # type: ignore[attr-defined]
