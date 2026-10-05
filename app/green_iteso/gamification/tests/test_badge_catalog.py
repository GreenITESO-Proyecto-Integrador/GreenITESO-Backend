"""Comprehensive tests for the badge catalog endpoint and selector."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import UserProfile
from green_iteso.gamification.models import Badge, UserBadge

User = get_user_model()


@pytest.fixture
def authenticated_client() -> tuple[APIClient, User]:
    client = APIClient()
    user = User.objects.create_user(
        email="badge_tester@example.com",
        password="password123",
    )
    UserProfile.objects.create(user=user)
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
def test_badge_catalog_list_returns_earned_status(
    authenticated_client: tuple[APIClient, User],
) -> None:
    client, user = authenticated_client

    earned_badge = Badge.objects.create(
        name="Eco Pioneer",
        description="Earned badge",
        points_required=50,
        is_active=True,
    )
    unearned_badge = Badge.objects.create(
        name="Eco Legend",
        description="Unearned badge",
        points_required=500,
        is_active=True,
    )

    UserBadge.objects.create(user=user, badge=earned_badge)

    response = client.get("/api/v1/rankings/badges/")

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 2

    badges_by_id = {b["id"]: b for b in response.data}

    assert badges_by_id[str(earned_badge.id)]["is_earned"] is True
    assert badges_by_id[str(earned_badge.id)]["earned_at"] is not None
    assert badges_by_id[str(unearned_badge.id)]["is_earned"] is False
    assert badges_by_id[str(unearned_badge.id)]["earned_at"] is None


@pytest.mark.django_db
def test_badge_catalog_excludes_inactive_badges(
    authenticated_client: tuple[APIClient, User],
) -> None:
    client, _ = authenticated_client

    Badge.objects.create(
        name="Active Badge",
        description="Visible badge",
        points_required=10,
        is_active=True,
    )
    Badge.objects.create(
        name="Inactive Badge",
        description="Hidden badge",
        points_required=10,
        is_active=False,
    )

    response = client.get("/api/v1/rankings/badges/")

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1
    assert response.data[0]["name"] == "Active Badge"


@pytest.mark.django_db
def test_badge_catalog_user_isolation(
    authenticated_client: tuple[APIClient, User],
) -> None:
    client, _ = authenticated_client

    user_b = User.objects.create_user(
        email="other_user@example.com",
        password="password123",
    )
    UserProfile.objects.create(user=user_b)

    badge = Badge.objects.create(
        name="Shared Target Badge",
        description="Target badge",
        points_required=100,
        is_active=True,
    )

    # Award badge only to User B
    UserBadge.objects.create(user=user_b, badge=badge)

    # Current user requests the catalog
    response = client.get("/api/v1/rankings/badges/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data[0]["is_earned"] is False
    assert response.data[0]["earned_at"] is None


@pytest.mark.django_db
def test_badge_catalog_requires_authentication() -> None:
    client = APIClient()
    response = client.get("/api/v1/rankings/badges/")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_badge_catalog_ordering(
    authenticated_client: tuple[APIClient, User],
) -> None:
    client, _ = authenticated_client

    Badge.objects.create(name="Zeta Badge", points_required=100, is_active=True)
    Badge.objects.create(name="Alpha Badge", points_required=100, is_active=True)
    Badge.objects.create(name="First Badge", points_required=10, is_active=True)

    response = client.get("/api/v1/rankings/badges/")

    assert response.status_code == status.HTTP_200_OK
    names = [b["name"] for b in response.data]
    assert names == ["First Badge", "Alpha Badge", "Zeta Badge"]
