"""Coverage for the /api/v1/users/me/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User


@pytest.mark.django_db
def test_me_requires_authentication() -> None:
    response = APIClient().get("/api/v1/users/me/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_returns_the_authenticated_caller_and_not_another_account() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    User.objects.create_user(email="zoe@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get("/api/v1/users/me/")

    assert response.status_code == 200
    assert response.json()["email"] == "ana@iteso.mx"


@pytest.mark.django_db
def test_list_users_requires_admin_role() -> None:
    student = User.objects.create_user(email="student@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(student)

    response = client.get("/api/v1/users/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_list_users_returns_all_accounts_for_admin() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(admin)

    response = client.get("/api/v1/users/")

    assert response.status_code == 200
    emails = {row["email"] for row in response.json()["results"]}
    assert emails == {"admin@iteso.mx", "ana@iteso.mx"}
