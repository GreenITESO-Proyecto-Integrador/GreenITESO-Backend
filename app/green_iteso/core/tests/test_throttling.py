"""Auth throttles key on the person, with a per-IP ceiling on top (#186)."""

from __future__ import annotations

from collections.abc import Iterator

import jwt
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from green_iteso.accounts.models import User

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
CAMPUS_IP = "203.0.113.10"
ATTACKER_IP = "198.51.100.99"


@pytest.fixture(autouse=True)
def _rates(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # DRF reads DEFAULT_THROTTLE_RATES into a class attribute at import time,
    # so override_settings does not reach it; patch the shared base instead.
    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {"auth_login": "2/min", "auth_refresh": "2/min", "auth_ip": "5/min"},
    )
    cache.clear()
    yield
    cache.clear()


def _id_token(oid: str) -> str:
    # Unsigned on purpose: the throttle only reads the claim, it never trusts it.
    return jwt.encode(
        {"oid": oid}, "not-a-real-key-for-throttle-tests", algorithm="HS256"
    )


def _login(client: APIClient, oid: str, ip: str = CAMPUS_IP) -> int:
    return client.post(
        LOGIN_URL,
        {"id_token": _id_token(oid), "access_token": ""},
        format="json",
        REMOTE_ADDR=ip,
    ).status_code


def _refresh(client: APIClient, token: str, ip: str = CAMPUS_IP) -> int:
    return client.post(
        REFRESH_URL, {"refresh": token}, format="json", REMOTE_ADDR=ip
    ).status_code


@pytest.mark.django_db
def test_accounts_behind_one_ip_do_not_share_the_login_quota() -> None:
    client = APIClient()

    statuses = [_login(client, oid) for oid in ("ana", "ana", "beto", "beto")]

    assert 429 not in statuses


@pytest.mark.django_db
def test_one_account_is_limited_on_its_ip() -> None:
    client = APIClient()

    statuses = [_login(client, "ana") for _ in range(3)]

    assert statuses == [401, 401, 429]


@pytest.mark.django_db
def test_a_forged_oid_from_another_ip_does_not_spend_the_victims_login_quota() -> None:
    client = APIClient()
    for _ in range(3):
        _login(client, "victim", ip=ATTACKER_IP)

    assert _login(client, "victim") == 401


@pytest.mark.django_db
def test_the_per_ip_ceiling_caps_rotating_accounts() -> None:
    client = APIClient()

    statuses = [_login(client, f"user-{n}") for n in range(6)]

    assert statuses == [401] * 5 + [429]


@pytest.mark.django_db
def test_requests_rejected_per_account_do_not_spend_the_ip_ceiling() -> None:
    client = APIClient()
    for _ in range(6):
        _login(client, "ana")

    assert _login(client, "beto") == 401


@pytest.mark.django_db
def test_refresh_is_limited_per_user_not_per_ip() -> None:
    first = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    second = User.objects.create_user(email="beto@iteso.mx", password="local-only")
    client = APIClient()

    def refresh(user: User) -> int:
        return _refresh(client, str(RefreshToken.for_user(user)))

    statuses = [refresh(first), refresh(first), refresh(second), refresh(second)]
    assert statuses == [200, 200, 200, 200]
    assert refresh(first) == 429


@pytest.mark.django_db
def test_a_forged_refresh_token_does_not_spend_the_victims_quota() -> None:
    victim = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    forged = jwt.encode(
        {
            "token_type": "refresh",
            "user_id": str(victim.pk),
            "jti": "x",
            "exp": 4102444800,
        },
        "attacker-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    client = APIClient()
    for _ in range(3):
        _refresh(client, forged, ip=ATTACKER_IP)

    statuses = [_refresh(client, str(RefreshToken.for_user(victim))) for _ in range(2)]

    assert statuses == [200, 200]
