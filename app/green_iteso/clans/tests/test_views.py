"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import create_clan


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
def test_select_active_clan_requires_authentication() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )

    response = APIClient().post(f"/api/v1/clans/{clan.pk}/select-active/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_member_selects_their_active_private_clan() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    client = APIClient()
    client.force_authenticate(owner)

    response = client.post(f"/api/v1/clans/{clan.pk}/select-active/")

    assert response.status_code == 200
    assert response.json()["is_active_private"] is True


@pytest.mark.django_db
def test_select_active_clan_rejects_a_non_member() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.post(f"/api/v1/clans/{clan.pk}/select-active/")

    assert response.status_code == 400
    assert not ClanMembership.objects.filter(
        user=outsider, clan=clan, is_active_private=True
    ).exists()


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
