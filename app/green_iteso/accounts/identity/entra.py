"""Microsoft Entra ID provider: verify the ID token, then enrich through Graph."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

import httpx
import jwt
from jwt import PyJWKClient

from green_iteso.accounts.exceptions import (
    IdentityProviderUnavailableError,
    InvalidIdentityTokenError,
)

from .base import ExternalIdentity

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_PROFILE_FIELDS = (
    "id,givenName,surname,mail,userPrincipalName,jobTitle,department,employeeId"
)
_MAX_GROUP_PAGES = 3


class _SigningKey(Protocol):
    key: Any


class _KeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey: ...


class EntraProvider:
    """Single-tenant Entra ID login for a SPA that sends both tokens.

    The ID token proves who the user is (signature, issuer, audience, tenant).
    The access token is only used to read the user's own Graph data; Graph is
    best-effort, except that a profile belonging to a different object id
    invalidates the login.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        key_resolver: _KeyResolver | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        # Normalized once so every derived URL/comparison agrees. Microsoft
        # always lowercases the `tid`/`iss` claims it issues, so building
        # `_issuer` from a raw, possibly-mixed-case env value would silently
        # fail every login with a confusing 401.
        self._tenant_id = tenant_id.lower()
        self._client_id = client_id
        self._issuer = f"https://login.microsoftonline.com/{self._tenant_id}/v2.0"
        self._keys: _KeyResolver = key_resolver or PyJWKClient(
            f"https://login.microsoftonline.com/{self._tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
            timeout=5,
        )
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(3.0))

    def authenticate(self, *, id_token: str, access_token: str) -> ExternalIdentity:
        claims = self._verify_id_token(id_token)
        oid = claims["oid"]
        profile = self._fetch_profile(access_token, oid)
        groups = self._fetch_group_ids(access_token) if profile is not None else None

        # Graph's own profile (already cross-checked against `oid` above) is
        # more authoritative than the ID token's self-reported claims, and
        # `userPrincipalName` is Entra's stable sign-in identifier -- more
        # reliable than the optional, unverified `email` claim. Token claims
        # are still the fallback for when Graph is unreachable.
        email = _first_text(
            profile.get("mail") if profile else None,
            profile.get("userPrincipalName") if profile else None,
            claims.get("email"),
            claims.get("preferred_username"),
        )
        if not email:
            raise InvalidIdentityTokenError(
                "The Microsoft account has no email address."
            )

        if profile is None:
            return ExternalIdentity(
                oid=oid,
                tenant_id=self._tenant_id,
                email=email,
                given_name=claims.get("given_name"),
                surname=claims.get("family_name"),
            )
        return ExternalIdentity(
            oid=oid,
            tenant_id=self._tenant_id,
            email=email,
            given_name=profile.get("givenName") or "",
            surname=profile.get("surname") or "",
            job_title=profile.get("jobTitle") or "",
            department=profile.get("department") or "",
            employee_id=profile.get("employeeId") or "",
            group_ids=groups,
        )

    def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        try:
            signing_key = self._keys.get_signing_key_from_jwt(id_token)
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailableError() from exc
        except jwt.PyJWTError as exc:
            raise InvalidIdentityTokenError() from exc
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise InvalidIdentityTokenError() from exc
        if str(claims.get("tid", "")).lower() != self._tenant_id:
            raise InvalidIdentityTokenError()
        try:
            claims["oid"] = str(uuid.UUID(str(claims.get("oid", ""))))
        except ValueError as exc:
            raise InvalidIdentityTokenError() from exc
        return claims

    def _fetch_profile(self, access_token: str, oid: str) -> dict[str, Any] | None:
        response = self._graph_get(
            f"{GRAPH_BASE}/me", access_token, {"$select": _PROFILE_FIELDS}
        )
        if response is None:
            return None
        profile: dict[str, Any] = response
        if str(profile.get("id", "")).lower() != oid:
            raise InvalidIdentityTokenError(
                "The access token belongs to a different user."
            )
        return profile

    def _fetch_group_ids(self, access_token: str) -> tuple[str, ...] | None:
        url: str | None = f"{GRAPH_BASE}/me/transitiveMemberOf/microsoft.graph.group"
        params: dict[str, str] | None = {"$select": "id", "$top": "999"}
        group_ids: list[str] = []
        for _ in range(_MAX_GROUP_PAGES):
            page = self._graph_get(url, access_token, params)
            if page is None:
                return None
            group_ids.extend(str(g["id"]) for g in page.get("value", []) if "id" in g)
            url = page.get("@odata.nextLink")
            params = None
            # Never follow a link off Graph with the user's bearer token.
            if not url or not url.startswith(f"{GRAPH_BASE}/"):
                break
        return tuple(group_ids)

    def _graph_get(
        self, url: str | None, access_token: str, params: dict[str, str] | None
    ) -> dict[str, Any] | None:
        if url is None:
            return None
        try:
            response = self._http.get(
                url, params=params, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError as exc:
            logger.warning("Graph request failed: %s", type(exc).__name__)
            return None
        if response.status_code != 200:
            logger.warning("Graph request returned HTTP %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None


def _first_text(*candidates: object) -> str:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""
