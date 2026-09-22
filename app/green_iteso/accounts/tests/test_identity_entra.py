"""Coverage for EntraProvider's ID-token verification and Graph enrichment."""

from __future__ import annotations

from typing import Any

import httpx
import jwt
import pytest

from green_iteso.accounts.exceptions import (
    IdentityProviderUnavailableError,
    InvalidIdentityTokenError,
)
from green_iteso.accounts.identity.entra import EntraProvider

from .identity_helpers import (
    CLIENT_ID,
    ISSUER,
    TENANT_ID,
    RecordingTransport,
    StubKeyResolver,
    make_id_token,
    make_keypair,
)


def _provider(
    public_key: Any,
    responses: dict[str, httpx.Response] | None = None,
    *,
    fail_keys: bool = False,
) -> EntraProvider:
    transport = RecordingTransport(responses or {})
    return EntraProvider(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        key_resolver=StubKeyResolver(public_key, fail=fail_keys),
        http_client=httpx.Client(transport=transport),
    )


def _profile_response(oid: str, **overrides: object) -> httpx.Response:
    body = {
        "id": oid,
        "givenName": "Ana",
        "surname": "García",
        "mail": "student@iteso.mx",
        "userPrincipalName": "student@iteso.mx",
        "jobTitle": "",
        "department": "Ingeniería de Software",
        "employeeId": "A01234567",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _groups_response(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"value": [{"id": i} for i in ids]})


def test_valid_token_returns_identity_with_graph_data() -> None:
    private_key, public_key = make_keypair()
    oid = "33333333-3333-3333-3333-333333333333"
    token = make_id_token(private_key, oid=oid)
    provider = _provider(
        public_key,
        {
            "/v1.0/me": _profile_response(oid),
            "/v1.0/me/transitiveMemberOf/microsoft.graph.group": _groups_response(
                "g1", "g2"
            ),
        },
    )

    identity = provider.authenticate(id_token=token, access_token="graph-token")

    assert identity.oid == oid
    assert identity.email == "student@iteso.mx"
    assert identity.department == "Ingeniería de Software"
    assert identity.group_ids == ("g1", "g2")


def test_expired_token_is_rejected() -> None:
    private_key, public_key = make_keypair()
    token = make_id_token(private_key, expired=True)
    provider = _provider(public_key)

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="t")


def test_wrong_audience_is_rejected() -> None:
    private_key, public_key = make_keypair()
    token = make_id_token(private_key, audience="someone-else")
    provider = _provider(public_key)

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="t")


def test_wrong_issuer_is_rejected() -> None:
    private_key, public_key = make_keypair()
    token = make_id_token(
        private_key, issuer="https://login.microsoftonline.com/other/v2.0"
    )
    provider = _provider(public_key)

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="t")


def test_wrong_tenant_claim_is_rejected() -> None:
    private_key, public_key = make_keypair()
    token = make_id_token(private_key, tenant_id="44444444-4444-4444-4444-444444444444")
    provider = _provider(public_key)

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="t")


def test_unsigned_alg_none_is_rejected() -> None:
    _, public_key = make_keypair()
    provider = _provider(public_key)
    unsigned = jwt.encode(
        {
            "oid": "oid",
            "tid": TENANT_ID,
            "aud": CLIENT_ID,
            "iss": ISSUER,
            "exp": 9999999999,
            "iat": 1,
            "sub": "s",
        },
        key=None,
        algorithm="none",
    )

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=unsigned, access_token="t")


def test_hs256_token_is_rejected() -> None:
    _, public_key = make_keypair()
    provider = _provider(public_key)
    forged = jwt.encode(
        {
            "oid": "oid",
            "tid": TENANT_ID,
            "aud": CLIENT_ID,
            "iss": ISSUER,
            "exp": 9999999999,
            "iat": 1,
            "sub": "s",
        },
        key="attacker-guessed-secret",
        algorithm="HS256",
    )

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=forged, access_token="t")


def test_missing_oid_is_rejected() -> None:
    private_key, public_key = make_keypair()
    token = jwt.encode(
        {
            "tid": TENANT_ID,
            "aud": CLIENT_ID,
            "iss": ISSUER,
            "exp": 9999999999,
            "iat": 1,
            "sub": "s",
        },
        private_key,
        algorithm="RS256",
    )
    provider = _provider(public_key)

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="t")


def test_graph_profile_owned_by_a_different_user_is_rejected() -> None:
    private_key, public_key = make_keypair()
    oid = "33333333-3333-3333-3333-333333333333"
    token = make_id_token(private_key, oid=oid)
    other_oid = "55555555-5555-5555-5555-555555555555"
    provider = _provider(public_key, {"/v1.0/me": _profile_response(other_oid)})

    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="stolen-token")


def test_graph_timeout_is_best_effort_and_login_still_succeeds() -> None:
    private_key, public_key = make_keypair()
    oid = "33333333-3333-3333-3333-333333333333"
    token = make_id_token(private_key, oid=oid)

    class TimingOutTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("graph is down", request=request)

    provider = EntraProvider(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        key_resolver=StubKeyResolver(public_key),
        http_client=httpx.Client(transport=TimingOutTransport()),
    )

    identity = provider.authenticate(id_token=token, access_token="t")

    assert identity.oid == oid
    assert identity.email == "student@iteso.mx"
    assert identity.department is None
    assert identity.group_ids is None


def test_graph_5xx_is_best_effort_and_login_still_succeeds() -> None:
    private_key, public_key = make_keypair()
    oid = "33333333-3333-3333-3333-333333333333"
    token = make_id_token(private_key, oid=oid)
    provider = _provider(public_key, {"/v1.0/me": httpx.Response(503, text="down")})

    identity = provider.authenticate(id_token=token, access_token="t")

    assert identity.oid == oid
    assert identity.department is None


def test_jwks_endpoint_unreachable_raises_service_unavailable() -> None:
    private_key, public_key = make_keypair()
    token = make_id_token(private_key)
    provider = _provider(public_key, fail_keys=True)

    with pytest.raises(IdentityProviderUnavailableError):
        provider.authenticate(id_token=token, access_token="t")
