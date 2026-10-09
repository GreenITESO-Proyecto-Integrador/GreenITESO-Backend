"""Shared builders for the actions domain tests."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant, Mission
from green_iteso.gamification.models import Badge, UserBadge
from green_iteso.notifications.models import Notification

ACTION_LOGS_URL = "/api/v1/action-logs/"


def create_student(email: str = "student@iteso.mx") -> User:
    """Create a user with a profile in a fresh institutional clan."""
    user = User.objects.create_user(email=email, password="local-only")
    clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=clan)
    return user


def create_admin(email: str = "admin@iteso.mx") -> User:
    """Create a user with the global ADMIN role."""
    return User.objects.create_user(
        email=email, password="local-only", role=User.Role.ADMIN
    )


def create_bike_action(points: int = 50) -> ActionMaster:
    """Create a declarative bike action in its own category."""
    category = ActionCategory.objects.create(code="MOBILITY", name="Movilidad")
    return ActionMaster.objects.create(
        code="BIKE",
        category=category,
        name="Uso de Bicicleta",
        description="Llegar en bici al campus",
        points=points,
        validation_type=ActionMaster.ValidationType.NONE,
    )


def create_mission_for(
    user: User, action: ActionMaster, target_count: int = 2
) -> Mission:
    """Create a running campaign joined by ``user`` with one mission for ``action``."""
    now = timezone.now()
    campaign = Campaign.objects.create(
        title="Semana de la bici",
        scope=Campaign.Scope.GLOBAL,
        status=Campaign.Status.IN_PROGRESS,
        creator=user,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=7),
    )
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    return Mission.objects.create(
        campaign=campaign, action=action, target_count=target_count
    )


def post_action_log(
    user: User, action: ActionMaster, evidence_object_key: str = ""
) -> Response:
    """Log ``action`` for ``user`` through the API with a fresh idempotency key."""
    payload = {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())}
    if evidence_object_key:
        payload["evidence_object_key"] = evidence_object_key
    client = APIClient()
    client.force_authenticate(user)
    return client.post(ACTION_LOGS_URL, payload, format="json")


def audit_action_log(
    admin: User, log_id: str, decision: str, reason: str = ""
) -> Response:
    """Send an audit decision for ``log_id`` as ``admin``."""
    payload = {"status": decision}
    if reason:
        payload["rejection_reason"] = reason
    client = APIClient()
    client.force_authenticate(admin)
    return client.patch(f"{ACTION_LOGS_URL}{log_id}/audit/", payload, format="json")


def assert_single_badge_award(user: User, badge: Badge) -> None:
    """Assert ``badge`` was granted once and announced with one notification."""
    assert UserBadge.objects.filter(user=user, badge=badge).count() == 1
    assert (
        Notification.objects.filter(
            user=user, notification_type=Notification.NotificationType.BADGE_EARNED
        ).count()
        == 1
    )
