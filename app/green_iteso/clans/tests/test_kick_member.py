"""Coverage for the kick member functionality (T2-41)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import ClanMembership, User
from green_iteso.clans.services import create_private_clan


@pytest.mark.django_db
def test_leader_can_kick_accepted_member(leader: User) -> None:
    """Test that a clan leader can successfully kick an accepted member from their clan."""
    clan = create_private_clan(name="Test Clan", created_by=leader)
    target_user = User.objects.create_user(
        email="member@iteso.mx", password="local-only"
    )
    # Simular membresía aceptada
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
    """Test that the clan's total points remain unchanged after a member is kicked."""
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
    assert clan.total_points == 500  # BR-08: Puntos intactos en el clan


@pytest.mark.django_db
def test_leader_cannot_kick_self(leader: User) -> None:
    """Test that a clan leader cannot kick themselves from their own clan."""
    clan = create_private_clan(name="Test Clan", created_by=leader)

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-kick-member", kwargs={"pk": clan.id})

    response = client.post(url, data={"user_id": str(leader.id)})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "user_id" in response.data


@pytest.mark.django_db
def test_non_leader_cannot_kick_member(leader: User) -> None:
    """Test that a non-leader member cannot kick another member from the clan."""
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
def test_admin_can_kick_member(leader: User) -> None:
    """Test that an admin can kick a member from any clan."""
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

    assert response.status_code == status.HTTP_204_NO_CONTENT
