"""Auth throttles key on the account, with a per-IP ceiling on top (#186)."""

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
    return jwt.encode({"oid": oid}, "not-a-real-key", algorithm="HS256")


def _login(client: APIClient, oid: str, ip: str = CAMPUS_IP) -> int:
    return client.post(
        LOGIN_URL,
        {"id_token": _id_token(oid), "access_token": ""},
        format="json",
        REMOTE_ADDR=ip,
    ).status_code


@pytest.mark.django_db
def test_accounts_behind_one_ip_do_not_share_the_login_quota() -> None:
    client = APIClient()

    statuses = [_login(client, oid) for oid in ("ana", "ana", "beto", "beto")]

    assert 429 not in statuses


@pytest.mark.django_db
def test_one_account_is_limited_across_ips() -> None:
    client = APIClient()

    statuses = [_login(client, "ana", ip=f"198.51.100.{n}") for n in range(3)]

    assert statuses[:2] != [429, 429]
    assert statuses[2] == 429


@pytest.mark.django_db
def test_the_per_ip_ceiling_caps_rotating_accounts() -> None:
    client = APIClient()

    statuses = [_login(client, f"user-{n}") for n in range(6)]

    assert 429 not in statuses[:5]
    assert statuses[5] == 429


@pytest.mark.django_db
def test_refresh_is_limited_per_user_not_per_ip() -> None:
    first = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    second = User.objects.create_user(email="beto@iteso.mx", password="local-only")
    client = APIClient()

    def refresh(user: User) -> int:
        token = str(RefreshToken.for_user(user))
        return client.post(
            REFRESH_URL, {"refresh": token}, format="json", REMOTE_ADDR=CAMPUS_IP
        ).status_code

    statuses = [refresh(first), refresh(first), refresh(second), refresh(second)]
    assert statuses == [200, 200, 200, 200]
    assert refresh(first) == 429
