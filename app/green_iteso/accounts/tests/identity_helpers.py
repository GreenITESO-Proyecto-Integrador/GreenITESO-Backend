"""Shared RSA/JWT fixtures for Entra provider tests (not collected by pytest)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"


@dataclass
class _StubKey:
    key: Any


class StubKeyResolver:
    """Stands in for ``PyJWKClient`` and always returns the test public key."""

    def __init__(self, public_key: Any, *, fail: bool = False) -> None:
        self._public_key = public_key
        self._fail = fail

    def get_signing_key_from_jwt(  # pylint: disable=unused-argument
        self, token: str
    ) -> _StubKey:
        """Ignore ``token``: this stub always answers with the same test key."""
        if self._fail:
            raise jwt.PyJWKClientConnectionError("could not reach the JWKS endpoint")
        return _StubKey(self._public_key)


def make_keypair() -> tuple[rsa.RSAPrivateKey, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def make_id_token(  # pylint: disable=too-many-arguments
    private_key: rsa.RSAPrivateKey,
    *,
    oid: str | None = None,
    tenant_id: str = TENANT_ID,
    audience: str = CLIENT_ID,
    issuer: str = ISSUER,
    email: str | None = "student@iteso.mx",
    given_name: str = "Ana",
    family_name: str = "García",
    expired: bool = False,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "oid": oid or str(uuid.uuid4()),
        "tid": tenant_id,
        "aud": audience,
        "iss": issuer,
        "iat": now - 60 if not expired else now - 7200,
        "exp": now + 3600 if not expired else now - 3600,
        "sub": "sub-value",
        "given_name": given_name,
        "family_name": family_name,
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, private_key, algorithm=algorithm)


class RecordingTransport(httpx.BaseTransport):
    """Serves canned Graph responses keyed by request path."""

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.get(request.url.path)
        if response is None:
            return httpx.Response(404, json={"error": "not stubbed"})
        return response
