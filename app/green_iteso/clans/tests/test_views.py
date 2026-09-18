"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User


@pytest.mark.django_db
def test_list_clans_requires_authentication() -> None:
    response = APIClient().get("/api/v1/clans/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_create_and_list_clan_for_authenticated_caller() -> None:
    caller = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    create_response = client.post(
        "/api/v1/clans/",
        {"name": "Green Team", "type": Clan.ClanType.PRIVATE},
    )
    assert create_response.status_code == 201

    list_response = client.get("/api/v1/clans/")
    names = [row["name"] for row in list_response.json()["results"]]
    assert names == ["Green Team"]
@pytest.mark.django_db
def test_institutional_clan_assignment_requires_authentication() -> None:
    response = APIClient().post(
        "/api/v1/clans/institutional-clan/", {"career": "Ingeniería en Sistemas"}
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_institutional_clan_assignment_declares_a_career() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.post(
        "/api/v1/clans/institutional-clan/", {"career": "Ingeniería en Sistemas"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["career"] == "Ingeniería en Sistemas"
    assert body["institutional_clan"]["name"] == "Ingeniería en Sistemas"
    assert body["onboarding_completed_at"] is not None


@pytest.mark.django_db
def test_institutional_clan_assignment_rejects_a_blank_career() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.post("/api/v1/clans/institutional-clan/", {"career": ""})

    assert response.status_code == 400


@pytest.mark.django_db
def test_institutional_clan_assignment_get_returns_current_state() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)
    client.post(
        "/api/v1/clans/institutional-clan/", {"career": "Ingeniería en Sistemas"}
    )

    response = client.get("/api/v1/clans/institutional-clan/")

    assert response.status_code == 200
    assert response.json()["career"] == "Ingeniería en Sistemas"