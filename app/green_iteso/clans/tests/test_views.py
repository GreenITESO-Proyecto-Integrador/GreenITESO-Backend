"""Coverage for the /api/v1/clans/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import create_clan, create_private_clan


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
def test_transfer_leadership_requires_authentication(clan: Clan) -> None:
    response = APIClient().post(f"/api/v1/clans/{clan.pk}/transfer-leadership/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_leader_transfers_leadership_successfully(
    leader: User, successor: User, clan: Clan
) -> None:
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
def test_member_cannot_transfer_leadership(
    leader: User, member: User, successor: User, clan: Clan
) -> None:
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
def test_transfer_leadership_rejects_a_non_member_successor(
    leader: User, clan: Clan
) -> None:
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(leader)

    response = client.post(
        f"/api/v1/clans/{clan.pk}/transfer-leadership/",
        {"successor_id": str(outsider.pk)},
    )

    assert response.status_code == 400


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
def test_dissolve_clan_requires_authentication(clan: Clan) -> None:
    response = APIClient().delete(f"/api/v1/clans/{clan.pk}/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401
    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_leader_dissolves_clan_and_it_leaves_the_listing(
    leader: User, clan: Clan
) -> None:
    client = APIClient()
    client.force_authenticate(leader)

    response = client.delete(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 204
    assert client.get("/api/v1/clans/").json()["results"] == []
    # The default manager excludes soft-deleted clans; use all_objects to
    # confirm the row was preserved rather than hard-deleted.
    assert Clan.all_objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_member_cannot_dissolve_clan(member: User, clan: Clan) -> None:
    ClanMembership.objects.create(user=member, clan=clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.delete(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolved_clan_is_not_retrievable(leader: User, clan: Clan) -> None:
    client = APIClient()
    client.force_authenticate(leader)
    client.delete(f"/api/v1/clans/{clan.pk}/")

    assert client.get(f"/api/v1/clans/{clan.pk}/").status_code == 404
    assert client.delete(f"/api/v1/clans/{clan.pk}/").status_code == 404


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
