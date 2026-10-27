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
        "/api/v1/clans/institutional-clan/",
        {
            "career": "Ingeniería en Sistemas",
            "nickname": "Ana",
            "visibility": "PRIVATE",
        },
    )

    response = client.get("/api/v1/clans/institutional-clan/")

    assert response.status_code == 200
    body = response.json()
    assert body["career"] == "Ingeniería en Sistemas"
    assert body["institutional_clan"]["name"] == "Ingeniería en Sistemas"
    assert body["nickname"] == "Ana"
    assert body["visibility"] == "PRIVATE"
    assert body["onboarding_completed_at"] is not None

    schema_response = client.get("/api/schema/")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    operation_schema = schema["paths"]["/api/v1/clans/institutional-clan/"]["get"]
    response_schema = operation_schema["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assignment_schema = schema["components"]["schemas"]["InstitutionalAssignment"]
    assert response_schema["$ref"] == "#/components/schemas/InstitutionalAssignment"
    assert {
        "career",
        "institutional_clan",
        "nickname",
        "visibility",
        "onboarding_completed_at",
    } <= assignment_schema["properties"].keys()


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


@pytest.mark.django_db
def test_join_requires_authentication(clan: Clan) -> None:
    response = APIClient().post(f"/api/v1/clans/{clan.pk}/join/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_join_public_clan_grants_accepted_membership() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_private_clan(name="Open Team", created_by=owner)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)

    response = client.post(f"/api/v1/clans/{clan.pk}/join/")

    assert response.status_code == 201
    assert response.json()["status"] == "ACCEPTED"


@pytest.mark.django_db
def test_join_private_invite_clan_creates_a_pending_request() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_private_clan(
        name="Secret Clan", created_by=owner, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)

    response = client.post(f"/api/v1/clans/{clan.pk}/join/")

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING"


@pytest.mark.django_db
def test_join_rejects_an_already_pending_request() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_private_clan(
        name="Secret Clan", created_by=owner, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)
    client.post(f"/api/v1/clans/{clan.pk}/join/")

    response = client.post(f"/api/v1/clans/{clan.pk}/join/")

    assert response.status_code == 400


@pytest.mark.django_db
def test_accept_request_requires_authentication(clan: Clan) -> None:
    response = APIClient().post(
        f"/api/v1/clans/{clan.pk}/accept-request/", {"applicant_id": str(clan.pk)}
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_leader_accepts_a_pending_join_request(leader: User) -> None:
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)
    client.post(f"/api/v1/clans/{clan.pk}/join/")

    client.force_authenticate(leader)
    response = client.post(
        f"/api/v1/clans/{clan.pk}/accept-request/",
        {"applicant_id": str(applicant.pk)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"


@pytest.mark.django_db
def test_member_cannot_accept_a_join_request(leader: User, member: User) -> None:
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=clan)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)
    client.post(f"/api/v1/clans/{clan.pk}/join/")

    client.force_authenticate(member)
    response = client.post(
        f"/api/v1/clans/{clan.pk}/accept-request/",
        {"applicant_id": str(applicant.pk)},
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_leader_rejects_a_pending_join_request(leader: User) -> None:
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)
    client.post(f"/api/v1/clans/{clan.pk}/join/")

    client.force_authenticate(leader)
    response = client.post(
        f"/api/v1/clans/{clan.pk}/reject-request/",
        {"applicant_id": str(applicant.pk)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


@pytest.mark.django_db
def test_leave_requires_authentication(clan: Clan) -> None:
    response = APIClient().post(f"/api/v1/clans/{clan.pk}/leave/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_member_leaves_a_clan(member: User, clan: Clan) -> None:
    ClanMembership.objects.create(user=member, clan=clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.post(f"/api/v1/clans/{clan.pk}/leave/")

    assert response.status_code == 204
    assert not ClanMembership.objects.filter(user=member, clan=clan).exists()


@pytest.mark.django_db
def test_member_cannot_leave_an_institutional_clan(member: User) -> None:
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )
    ClanMembership.objects.create(user=member, clan=institutional_clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.post(f"/api/v1/clans/{institutional_clan.pk}/leave/")

    assert response.status_code == 400
    assert ClanMembership.objects.filter(user=member, clan=institutional_clan).exists()


@pytest.mark.django_db
def test_leader_cannot_leave_their_own_clan(leader: User, clan: Clan) -> None:
    client = APIClient()
    client.force_authenticate(leader)

    response = client.post(f"/api/v1/clans/{clan.pk}/leave/")

    assert response.status_code == 403
    assert ClanMembership.objects.filter(user=leader, clan=clan).exists()


@pytest.mark.django_db
def test_retrieve_clan_excludes_a_pending_applicant_from_roster_and_count(
    leader: User,
) -> None:
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    client = APIClient()
    client.force_authenticate(applicant)
    client.post(f"/api/v1/clans/{clan.pk}/join/")

    client.force_authenticate(leader)
    response = client.get(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 200
    body = response.json()
    assert body["member_count"] == 1
    member_ids = [member["user_id"] for member in body["members"]]
    assert str(applicant.pk) not in member_ids


@pytest.mark.django_db
def test_edit_clan_requires_authentication(clan: Clan) -> None:
    response = APIClient().patch(f"/api/v1/clans/{clan.pk}/", {"name": "New"})

    assert response.status_code == 401


@pytest.mark.django_db
def test_leader_edits_clan_name_and_privacy(leader: User) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.patch(
        f"/api/v1/clans/{clan.pk}/",
        {"name": "Blue Team", "privacy": Clan.Privacy.PRIVATE_INVITE},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Blue Team"
    assert response.json()["privacy"] == Clan.Privacy.PRIVATE_INVITE
    clan.refresh_from_db()
    assert clan.name == "Blue Team"
    assert clan.privacy == Clan.Privacy.PRIVATE_INVITE


@pytest.mark.django_db
def test_edit_clan_ignores_read_only_fields(leader: User) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.patch(
        f"/api/v1/clans/{clan.pk}/",
        {"type": Clan.ClanType.INSTITUTIONAL, "total_points": 9999},
    )

    assert response.status_code == 200
    clan.refresh_from_db()
    assert clan.type == Clan.ClanType.PRIVATE
    assert clan.total_points == 0


@pytest.mark.django_db
def test_member_cannot_edit_clan(leader: User, member: User) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    ClanMembership.objects.create(user=member, clan=clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Hijacked"})

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.name == "Green Team"


@pytest.mark.django_db
def test_admin_cannot_edit_a_clan_they_do_not_lead(leader: User) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    admin = User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)
    client = APIClient()
    client.force_authenticate(admin)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Hijacked"})

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.name == "Green Team"


@pytest.mark.django_db
def test_edit_clan_rejects_a_duplicate_name(leader: User, member: User) -> None:
    create_private_clan(name="Taken", created_by=member)
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Taken"})

    assert response.status_code == 400
    assert "name" in response.json()


@pytest.mark.django_db
def test_edit_clan_rejects_an_invalid_privacy_on_the_privacy_field(
    leader: User,
) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"privacy": "SECRET"})

    assert response.status_code == 400
    assert "privacy" in response.json()


@pytest.mark.django_db
def test_edit_institutional_clan_is_forbidden(member: User) -> None:
    clan = Clan.objects.create(name="Arquitectura", type=Clan.ClanType.INSTITUTIONAL)
    ClanMembership.objects.create(user=member, clan=clan)
    client = APIClient()
    client.force_authenticate(member)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Renamed"})

    assert response.status_code == 403
    clan.refresh_from_db()
    assert clan.name == "Arquitectura"


@pytest.mark.django_db
def test_non_member_editing_an_invite_only_clan_gets_404(
    leader: User, member: User
) -> None:
    clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    client = APIClient()
    client.force_authenticate(member)

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Hijacked"})

    assert response.status_code == 404


@pytest.mark.django_db
def test_edit_dissolved_clan_returns_404(leader: User, clan: Clan) -> None:
    client = APIClient()
    client.force_authenticate(leader)
    client.delete(f"/api/v1/clans/{clan.pk}/")

    response = client.patch(f"/api/v1/clans/{clan.pk}/", {"name": "Revived"})

    assert response.status_code == 404
