"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
from green_iteso.clans.services import create_private_clan


@pytest.mark.django_db
def test_list_clans_requires_authentication() -> None:
    response = APIClient().get("/api/v1/clans/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


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

    assert response.status_code == 401


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


@pytest.mark.django_db
def test_list_clans_filters_by_search_query_param() -> None:
    first_leader = User.objects.create_user(
        email="lead1@iteso.mx", password="local-only"
    )
    second_leader = User.objects.create_user(
        email="lead2@iteso.mx", password="local-only"
    )
    client = APIClient()

    client.force_authenticate(first_leader)
    client.post("/api/v1/clans/", {"name": "Green Team"})

    client.force_authenticate(second_leader)
    client.post("/api/v1/clans/", {"name": "Blue Squad"})

    response = client.get("/api/v1/clans/?search=green")

    names = [row["name"] for row in response.json()["results"]]
    assert names == ["Green Team"]


@pytest.mark.django_db
def test_retrieve_clan_returns_member_roster() -> None:
    leader = User.objects.create_user(
        email="leader@iteso.mx", password="local-only", nickname="Leo"
    )
    clan = create_private_clan(name="Roster Test", created_by=leader)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.get(f"/api/v1/clans/{clan.id}/")

    assert response.status_code == 200
    body = response.json()
    assert body["member_count"] == 1
    assert body["members"][0]["nickname"] == "Leo"
    assert body["members"][0]["role"] == "LEADER"


@pytest.mark.django_db
def test_retrieve_private_invite_clan_404s_for_non_member() -> None:
    leader = User.objects.create_user(email="leader@iteso.mx", password="local-only")
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.get(f"/api/v1/clans/{clan.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_list_clans_excludes_private_invite_clan_for_non_member() -> None:
    leader = User.objects.create_user(email="leader@iteso.mx", password="local-only")
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.get("/api/v1/clans/")

    names = [row["name"] for row in response.json()["results"]]
    assert "Secret Clan" not in names
