"""End-to-end coverage for POST /api/v1/auth/login/ using the mock provider."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import AccessToken

from green_iteso.accounts.models import User

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"


@pytest.fixture(autouse=True)
def use_mock_identity_provider() -> Generator[None, None, None]:
    with override_settings(
        MICROSOFT_AUTH_MODE="mock",
        MICROSOFT_TENANT_ID="",
        MICROSOFT_CLIENT_ID="",
    ):
        cache.clear()
        yield
        cache.clear()


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
@pytest.mark.parametrize(
    ("email", "job_title", "expected_role"),
    [
        ("alumno@iteso.mx", "ALUMNO", "STUDENT"),
        ("estudiante@iteso.mx", "Estudiante", "STUDENT"),
        ("profesor@iteso.mx", "Profesor de Asignatura", "STAFF"),
        ("coordinadora@iteso.mx", "Coordinadora Académica", "STAFF"),
        ("personal@iteso.mx", "Personal Administrativo", "STAFF"),
    ],
)
def test_login_assigns_role_from_microsoft_job_title(
    email: str, job_title: str, expected_role: str
) -> None:
    token = "mock:" + json.dumps(
        {"email": email, "job_title": job_title},
        separators=(",", ":"),
    )

    response = APIClient().post(
        LOGIN_URL, {"id_token": token, "access_token": ""}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == expected_role
    assert User.objects.get(email=email).role == expected_role


@pytest.mark.django_db
def test_login_preserves_admin_role_regardless_of_microsoft_job_title() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx",
        password=None,
        role=User.Role.ADMIN,
    )
    token = "mock:" + json.dumps(
        {"email": admin.email, "job_title": "ALUMNO"},
        separators=(",", ":"),
    )

    response = APIClient().post(
        LOGIN_URL, {"id_token": token, "access_token": ""}, format="json"
    )

    assert response.status_code == 200
    admin.refresh_from_db()
    assert response.json()["user"]["role"] == User.Role.ADMIN
    assert admin.role == User.Role.ADMIN


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
def test_expired_access_token_is_rejected() -> None:
    """T2-11 AC: an expired access token responds 401, not a silent pass-through."""
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(seconds=-1))

    authenticated = APIClient()
    authenticated.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = authenticated.get("/api/v1/users/me/")

    assert response.status_code == 401


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
