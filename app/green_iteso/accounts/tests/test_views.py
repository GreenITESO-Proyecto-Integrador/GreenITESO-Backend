"""Coverage for the /api/v1/users/me/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User


@pytest.mark.django_db
def test_me_requires_authentication() -> None:
    response = APIClient().get("/api/v1/users/me/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_me_returns_the_authenticated_caller_and_not_another_account() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    User.objects.create_user(email="zoe@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get("/api/v1/users/me/")

    assert response.status_code == 200
    assert response.json()["email"] == "ana@iteso.mx"
