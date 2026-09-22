"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
from green_iteso.clans.services import MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER

from .conftest import join_user_to_new_private_clans


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
def test_create_clan_ignores_a_requested_institutional_type() -> None:
    """T2-31: this endpoint only ever creates PRIVATE clans."""
    caller = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.post(
        "/api/v1/clans/",
        {"name": "Green Team", "type": Clan.ClanType.INSTITUTIONAL},
    )

    assert response.status_code == 201
    assert response.json()["type"] == Clan.ClanType.PRIVATE


@pytest.mark.django_db
def test_create_clan_accepts_privacy_and_avatar() -> None:
    caller = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.post(
        "/api/v1/clans/",
        {
            "name": "Green Team",
            "type": Clan.ClanType.PRIVATE,
            "privacy": Clan.Privacy.PRIVATE_INVITE,
            "avatar_object_key": "clans/avatars/green-team.png",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["privacy"] == Clan.Privacy.PRIVATE_INVITE
    assert body["avatar_object_key"] == "clans/avatars/green-team.png"


@pytest.mark.django_db
def test_create_clan_rejects_a_second_leadership_for_the_same_caller() -> None:
    caller = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)
    client.post("/api/v1/clans/", {"name": "First Clan"})

    response = client.post("/api/v1/clans/", {"name": "Second Clan"})

    assert response.status_code == 400


@pytest.mark.django_db
def test_create_clan_rejects_a_sixth_private_clan_membership_for_the_caller() -> None:
    caller = User.objects.create_user(email="member@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)
    join_user_to_new_private_clans(caller, MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER)

    response = client.post("/api/v1/clans/", {"name": "One Too Many"})

    assert response.status_code == 400


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
