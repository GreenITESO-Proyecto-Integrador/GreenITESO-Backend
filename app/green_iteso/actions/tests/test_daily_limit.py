"""Issue #68: reject a registration that would exceed today's local quota."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogCreateView

MEXICO_CITY = ZoneInfo("America/Mexico_City")


def _user(email: str) -> User:
    user = User.objects.create_user(email=email, password="local-only")
    clan = Clan.objects.create(name=email, type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    return user


def _action(
    code: str,
    *,
    daily_limit: int = 1,
    validation_type: str = ActionMaster.ValidationType.NONE,
) -> ActionMaster:
    category = ActionCategory.objects.create(code=f"CAT-{code}", name=code)
    return ActionMaster.objects.create(
        code=code,
        category=category,
        name=code,
        description="Test action",
        points=10,
        daily_limit=daily_limit,
        validation_type=validation_type,
    )


def _register(user: User, action: ActionMaster) -> Response:
    request = APIRequestFactory().post(
        "/api/v1/action-logs/",
        {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )
    force_authenticate(request, user=user)
    return ActionLogCreateView.as_view()(request)


@pytest.mark.django_db
def test_second_registration_within_24_hours_is_rejected() -> None:
    user = _user("student@iteso.mx")
    action = _action("RECYCLE", daily_limit=1)

    first = _register(user, action)
    second = _register(user, action)

    assert first.status_code == 201
    assert second.status_code == 400
    assert second.data == {"error": "Daily action limit exceeded for today."}
    assert ActionLog.objects.filter(user=user, action=action).count() == 1
    user.profile.refresh_from_db()
    assert user.profile.total_points == 10
    assert user.profile.available_points == 10


@pytest.mark.django_db
def test_quota_allows_daily_limit_registrations_and_rejects_the_next() -> None:
    user = _user("quota@iteso.mx")
    action = _action("WATER", daily_limit=2)

    assert _register(user, action).status_code == 201
    assert _register(user, action).status_code == 201
    assert _register(user, action).status_code == 400
    assert ActionLog.objects.filter(user=user, action=action).count() == 2


@pytest.mark.django_db
def test_previous_local_evening_does_not_consume_todays_quota() -> None:
    """23:00 yesterday is the same rolling 24 hours at 10:00, but another day."""
    user = _user("yesterday@iteso.mx")
    action = _action("BIKE")
    previous_evening = datetime(2026, 10, 1, 23, 0, tzinfo=MEXICO_CITY)
    later_that_morning = datetime(2026, 10, 2, 10, 0, tzinfo=MEXICO_CITY)

    assert _register(user, action).status_code == 201
    ActionLog.objects.filter(user=user, action=action).update(
        created_at=previous_evening
    )

    with patch(
        "green_iteso.actions.services.timezone.now", return_value=later_that_morning
    ):
        assert _register(user, action).status_code == 201
    assert ActionLog.objects.filter(user=user, action=action).count() == 2


@pytest.mark.django_db
def test_early_morning_log_still_counts_later_the_same_local_day() -> None:
    user = _user("morning@iteso.mx")
    action = _action("WALK")
    early = datetime(2026, 10, 2, 0, 15, tzinfo=MEXICO_CITY)
    evening = datetime(2026, 10, 2, 20, 0, tzinfo=MEXICO_CITY)

    assert _register(user, action).status_code == 201
    ActionLog.objects.filter(user=user, action=action).update(created_at=early)

    with patch("green_iteso.actions.services.timezone.now", return_value=evening):
        assert _register(user, action).status_code == 400
    assert ActionLog.objects.filter(user=user, action=action).count() == 1


@pytest.mark.django_db
def test_rejected_log_does_not_consume_the_quota() -> None:
    user = _user("retry@iteso.mx")
    action = _action("PHOTO-RETRY", validation_type=ActionMaster.ValidationType.PHOTO)

    assert _register(user, action).status_code == 201
    ActionLog.objects.filter(user=user, action=action).update(
        status=ActionLog.Status.REJECTED
    )

    retry = _register(user, action)
    assert retry.status_code == 201
    assert ActionLog.objects.filter(user=user, action=action).count() == 2


@pytest.mark.django_db
def test_pending_audit_log_consumes_the_quota() -> None:
    user = _user("pending@iteso.mx")
    action = _action("PHOTO", validation_type=ActionMaster.ValidationType.PHOTO)

    assert _register(user, action).status_code == 201
    blocked = _register(user, action)

    assert blocked.status_code == 400
    assert (
        ActionLog.objects.filter(user=user, action=action).get().status
        == ActionLog.Status.PENDING_AUDIT
    )
    user.profile.refresh_from_db()
    assert user.profile.total_points == 0


@pytest.mark.django_db
def test_quota_is_per_user_and_per_action() -> None:
    owner = _user("owner@iteso.mx")
    other = _user("other@iteso.mx")
    recycle = _action("RECYCLE-A")
    plant = _action("PLANT-A")

    assert _register(owner, recycle).status_code == 201
    assert _register(other, recycle).status_code == 201
    assert _register(owner, plant).status_code == 201
    assert _register(owner, recycle).status_code == 400
