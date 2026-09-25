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
def test_transfer_leadership_requires_authentication() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    response = APIClient().post(f"/api/v1/clans/{clan.pk}/transfer-leadership/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_leader_transfers_leadership_successfully() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    successor = User.objects.create_user(
        email="successor@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    client = APIClient()
    client.force_authenticate(leader)

    response = client.post(
        f"/api/v1/clans/{clan.pk}/transfer-leadership/",
        {"successor_id": str(successor.pk)},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "LEADER"
    leader_membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert leader_membership.role == ClanMembership.MembershipRole.MEMBER


@pytest.mark.django_db
def test_member_cannot_transfer_leadership() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    successor = User.objects.create_user(
        email="successor@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.create(
        user=member, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    client = APIClient()
    client.force_authenticate(member)

    response = client.post(
        f"/api/v1/clans/{clan.pk}/transfer-leadership/",
        {"successor_id": str(successor.pk)},
    )

    assert response.status_code == 403
    leader_membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert leader_membership.role == ClanMembership.MembershipRole.LEADER


@pytest.mark.django_db
def test_transfer_leadership_rejects_a_non_member_successor() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    client = APIClient()
    client.force_authenticate(leader)

    response = client.post(
        f"/api/v1/clans/{clan.pk}/transfer-leadership/",
        {"successor_id": str(outsider.pk)},
    )

    assert response.status_code == 400


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
