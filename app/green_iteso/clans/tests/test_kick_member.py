"""Coverage for the kick member functionality (T2-41)."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    assign_institutional_clan,
    create_private_clan,
    dissolve_clan,
    kick_member,
)


# --- HTTP View Tests ---
@pytest.mark.django_db
def test_leader_can_kick_accepted_member(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)
    target_user = User.objects.create_user(
        email="member@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=target_user, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(target_user.id)})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not ClanMembership.objects.filter(user=target_user, clan=clan).exists()


@pytest.mark.django_db
def test_points_history_preserved_after_kick(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)
    clan.total_points = 500
    clan.save()

    target_user = User.objects.create_user(
        email="contributor@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=target_user, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(target_user.id)})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    clan.refresh_from_db()
    assert clan.total_points == 500


@pytest.mark.django_db
def test_leader_cannot_kick_self(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(leader.id)})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "user_id" in response.data


@pytest.mark.django_db
def test_non_leader_cannot_kick_member(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)
    member = User.objects.create_user(email="regular@iteso.mx", password="local-only")
    target_user = User.objects.create_user(
        email="target@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=member, clan=clan, status=ClanMembership.Status.ACCEPTED
    )
    ClanMembership.objects.create(
        user=target_user, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    client = APIClient()
    client.force_authenticate(user=member)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(target_user.id)})

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_admin_cannot_kick_member(leader: User) -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    clan = create_private_clan(name="Test Clan", created_by=leader)
    target_user = User.objects.create_user(
        email="target@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=target_user, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    client = APIClient()
    client.force_authenticate(user=admin)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(target_user.id)})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert ClanMembership.objects.filter(user=target_user, clan=clan).exists()


@pytest.mark.django_db
def test_cannot_kick_member_from_institutional_clan() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    inst_clan = Clan.objects.create(
        name="Ingeniería de Software",
        type=Clan.ClanType.INSTITUTIONAL,
        privacy=Clan.Privacy.PUBLIC,
    )
    target_user = User.objects.create_user(
        email="student@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=target_user, clan=inst_clan, status=ClanMembership.Status.ACCEPTED
    )

    client = APIClient()
    client.force_authenticate(user=admin)
    url = reverse("clan-kick-member", kwargs={"pk": inst_clan.id})

    response = client.post(url, data={"user_id": str(target_user.id)})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert ClanMembership.objects.filter(user=target_user, clan=inst_clan).exists()


# --- Service Direct Unit Tests ---
@pytest.mark.django_db
def test_service_refuses_institutional_clan_even_for_its_leader() -> None:
    student = User.objects.create_user(email="student@iteso.mx", password="local-only")
    profile = assign_institutional_clan(user=student, career="ISC")
    clan = profile.institutional_clan
    lead = User.objects.create_user(email="lead2@iteso.mx", password="local-only")
    ClanMembership.objects.create(
        user=lead,
        clan=clan,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
    )

    with pytest.raises(PermissionDenied) as exc_info:
        kick_member(clan=clan, actor=lead, target_user=student)

    assert "Only private clans allow expelling members." in str(exc_info.value)
    profile.refresh_from_db()
    assert profile.institutional_clan_id == clan.id
    assert ClanMembership.objects.filter(user=student, clan=clan).exists()


@pytest.mark.django_db
def test_service_refuses_non_leader(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    target = User.objects.create_user(email="target@iteso.mx", password="local-only")
    ClanMembership.objects.create(
        user=member, clan=clan, status=ClanMembership.Status.ACCEPTED
    )
    ClanMembership.objects.create(
        user=target, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    with pytest.raises(PermissionDenied) as exc_info:
        kick_member(clan=clan, actor=member, target_user=target)

    assert "Only the clan leader can expel members." in str(exc_info.value)
    assert ClanMembership.objects.filter(user=target, clan=clan).exists()


@pytest.mark.django_db
def test_service_refuses_dissolved_clan(leader: User) -> None:
    clan = create_private_clan(name="Test Clan", created_by=leader)
    target = User.objects.create_user(email="target@iteso.mx", password="local-only")
    ClanMembership.objects.create(
        user=target, clan=clan, status=ClanMembership.Status.ACCEPTED
    )

    dissolve_clan(clan=clan, actor=leader)

    with pytest.raises(Clan.DoesNotExist):
        kick_member(clan=clan, actor=leader, target_user=target)
