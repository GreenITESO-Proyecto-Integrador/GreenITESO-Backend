"""Tests for gamification services."""

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from green_iteso.accounts.models import UserProfile
from green_iteso.gamification.models import Badge, UserBadge
from green_iteso.gamification.services import check_and_award_badges
from green_iteso.notifications.models import Notification

User = get_user_model()


@pytest.mark.django_db
def test_check_and_award_badges_creates_badge_and_notification() -> None:
    user = User.objects.create_user(
        email="test@example.com",
        password="password123",
    )
    # Explicitly create the missing profile for the test user
    UserProfile.objects.create(user=user, total_points=100)

    badge = Badge.objects.create(
        name="Eco Starter",
        description="Earn 100 points",
        points_required=100,
        is_active=True,
    )

    check_and_award_badges(user)

    assert UserBadge.objects.filter(user=user, badge=badge).exists()
    assert Notification.objects.filter(
        user=user,
        notification_type=Notification.NotificationType.BADGE_EARNED,
    ).exists()


@pytest.mark.django_db
def test_check_and_award_badges_idempotent_on_concurrency() -> None:
    user = User.objects.create_user(
        email="concurrent@example.com",
        password="password123",
    )
    # Explicitly create the missing profile for the test user
    UserProfile.objects.create(user=user, total_points=200)

    badge = Badge.objects.create(
        name="Eco Master",
        description="Earn 200 points",
        points_required=200,
        is_active=True,
    )

    # Simulate consecutive concurrent calls
    check_and_award_badges(user)
    check_and_award_badges(user)

    assert UserBadge.objects.filter(user=user, badge=badge).count() == 1
    assert (
        Notification.objects.filter(
            user=user,
            notification_type=Notification.NotificationType.BADGE_EARNED,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_check_and_award_badges_pushes_the_notification_after_commit(
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    user = User.objects.create_user(
        email="live@example.com",
        password="password123",
    )
    UserProfile.objects.create(user=user, total_points=50)
    Badge.objects.create(
        name="Guardián del agua",
        description="Earn 50 points",
        points_required=50,
        is_active=True,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        check_and_award_badges(user)

    # One callback = the WebSocket push that lets the frontend show the celebration.
    assert len(callbacks) == 1
    notification = Notification.objects.get(user=user)
    assert notification.message == 'Obtuviste la insignia "Guardián del agua".'
