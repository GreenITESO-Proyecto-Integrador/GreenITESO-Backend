"""End-to-end coverage for POST /api/v1/auth/login/ using the mock provider."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from green_iteso.accounts.models import User

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"


@pytest.mark.django_db
def test_login_with_mock_token_returns_jwt_pair_and_creates_the_user() -> None:
    response = APIClient().post(
        LOGIN_URL, {"id_token": "mock:ana@iteso.mx", "access_token": ""}, format="json"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["user"]["email"] == "ana@iteso.mx"
    assert body["user"]["role"] == "STUDENT"
    assert body["access"] and body["refresh"]
    assert User.objects.filter(email="ana@iteso.mx").exists()


@pytest.mark.django_db
def test_issued_access_token_authenticates_a_follow_up_request() -> None:
    login = APIClient().post(
        LOGIN_URL, {"id_token": "mock:ana@iteso.mx", "access_token": ""}, format="json"
    )
    access = login.json()["access"]

    authenticated = APIClient()
    authenticated.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = authenticated.get("/api/v1/users/me/")

    assert response.status_code == 200
    assert response.json()["email"] == "ana@iteso.mx"


@pytest.mark.django_db
def test_refresh_token_exchanges_for_a_new_access_token() -> None:
    login = APIClient().post(
        LOGIN_URL, {"id_token": "mock:ana@iteso.mx", "access_token": ""}, format="json"
    )
    refresh = login.json()["refresh"]

    response = APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json")

    assert response.status_code == 200
    new_access = response.json()["access"]
    assert new_access

    authenticated = APIClient()
    authenticated.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access}")
    me_response = authenticated.get("/api/v1/users/me/")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "ana@iteso.mx"


@pytest.mark.django_db
def test_refresh_rejects_a_garbage_token() -> None:
    response = APIClient().post(
        REFRESH_URL, {"refresh": "not-a-real-token"}, format="json"
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_request_without_an_access_token_is_rejected() -> None:
    response = APIClient().get("/api/v1/users/me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_non_institutional_email_is_rejected_with_domain_error() -> None:
    response = APIClient().post(
        LOGIN_URL, {"id_token": "mock:ana@gmail.com", "access_token": ""}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DOMAIN_NOT_ALLOWED"


@pytest.mark.django_db
def test_malformed_body_returns_validation_error() -> None:
    response = APIClient().post(LOGIN_URL, {}, format="json")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "id_token" in body["error"]["message"]


@pytest.mark.django_db
def test_login_endpoint_does_not_require_authentication() -> None:
    response = APIClient().post(
        LOGIN_URL, {"id_token": "mock:bob@iteso.mx", "access_token": ""}, format="json"
    )

    assert response.status_code != 401


@pytest.mark.django_db
def test_repeated_logins_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    # DRF reads DEFAULT_THROTTLE_RATES into a class attribute once at import
    # time, so override_settings does not reach it; patch it directly instead.
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {"auth_login": "2/min"}, raising=False
    )
    cache.clear()
    client = APIClient()
    body = {"id_token": "mock:throttle@iteso.mx", "access_token": ""}

    responses = [client.post(LOGIN_URL, body, format="json") for _ in range(3)]

    assert responses[0].status_code == 200
    assert responses[1].status_code == 200
    assert responses[2].status_code == 429
    cache.clear()
