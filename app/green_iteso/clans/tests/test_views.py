"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import create_clan


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
def test_dissolve_clan_requires_authentication() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    response = APIClient().delete(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_leader_dissolves_clan_and_it_leaves_the_listing() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    client = APIClient()
    client.force_authenticate(leader)

    response = client.delete(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 204
    assert client.get("/api/v1/clans/").json()["results"] == []
    assert Clan.objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_member_cannot_dissolve_clan() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.create(user=member, clan=clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.delete(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolved_clan_is_not_retrievable() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    client = APIClient()
    client.force_authenticate(leader)
    client.delete(f"/api/v1/clans/{clan.pk}/")

    assert client.get(f"/api/v1/clans/{clan.pk}/").status_code == 404
    assert client.delete(f"/api/v1/clans/{clan.pk}/").status_code == 404
