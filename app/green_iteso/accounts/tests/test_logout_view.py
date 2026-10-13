"""End-to-end coverage for POST /api/v1/auth/logout/ (T2-11)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"


def _login() -> dict[str, str]:
    response = APIClient().post(
        LOGIN_URL, {"id_token": "mock:ana@iteso.mx", "access_token": ""}, format="json"
    )
    return response.json()


@pytest.mark.django_db
def test_logout_blacklists_the_refresh_token() -> None:
    tokens = _login()

    logout_response = APIClient().post(
        LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json"
    )
    assert logout_response.status_code == 200

    reuse_response = APIClient().post(
        REFRESH_URL, {"refresh": tokens["refresh"]}, format="json"
    )
    assert reuse_response.status_code == 401


@pytest.mark.django_db
def test_logout_does_not_require_an_access_token() -> None:
    """A caller whose access token already expired must still be able to log out."""
    tokens = _login()

    response = APIClient().post(
        LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json"
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_logout_requires_a_refresh_token() -> None:
    response = APIClient().post(LOGOUT_URL, {}, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_logout_rejects_an_invalid_refresh_token() -> None:
    response = APIClient().post(
        LOGOUT_URL, {"refresh": "not-a-real-token"}, format="json"
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_rotates_the_refresh_token_and_blacklists_the_old_one() -> None:
    tokens = _login()

    first_refresh_response = APIClient().post(
        REFRESH_URL, {"refresh": tokens["refresh"]}, format="json"
    )
    assert first_refresh_response.status_code == 200
    rotated_refresh = first_refresh_response.json()["refresh"]
    assert rotated_refresh != tokens["refresh"]

    reuse_response = APIClient().post(
        REFRESH_URL, {"refresh": tokens["refresh"]}, format="json"
    )
    assert reuse_response.status_code == 401

    second_refresh_response = APIClient().post(
        REFRESH_URL, {"refresh": rotated_refresh}, format="json"
    )
    assert second_refresh_response.status_code == 200
