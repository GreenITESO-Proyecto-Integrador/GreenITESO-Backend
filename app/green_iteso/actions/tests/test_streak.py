"""Coverage for the consecutive-day streak derived from approved action logs."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from green_iteso.accounts.models import User, UserProfile
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.actions.services import recalculate_streak

from .helpers import (
    audit_action_log,
    create_admin,
    create_bike_action,
    create_student,
    post_action_log,
)


def _log_on(
    user: User,
    action: ActionMaster,
    days_ago: int,
    *,
    status: str = ActionLog.Status.APPROVED,
) -> ActionLog:
    """Create a log dated ``days_ago`` days before today in the project timezone."""
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=user.profile.institutional_clan,
        idempotency_key=str(uuid.uuid4()),
        points_awarded=action.points,
        status=status,
    )
    # created_at is auto_now_add, so the date is moved afterwards.
    ActionLog.objects.filter(pk=log.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    return log


@pytest.mark.django_db
def test_a_single_active_day_is_a_streak_of_one() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 0)

    assert recalculate_streak(user) == 1

    user.profile.refresh_from_db()
    assert user.profile.current_streak == 1
    assert user.profile.last_action_date == timezone.localdate()


@pytest.mark.django_db
def test_consecutive_days_accumulate() -> None:
    user = create_student()
    action = create_bike_action()
    for days_ago in (0, 1, 2):
        _log_on(user, action, days_ago)

    assert recalculate_streak(user) == 3


@pytest.mark.django_db
def test_a_missing_day_breaks_the_streak() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 0)
    _log_on(user, action, 1)
    # Nothing on day 2; the older run must not be counted.
    _log_on(user, action, 3)
    _log_on(user, action, 4)

    assert recalculate_streak(user) == 2


@pytest.mark.django_db
def test_several_logs_on_one_day_count_once() -> None:
    user = create_student()
    action = create_bike_action()
    for _ in range(3):
        _log_on(user, action, 0)

    assert recalculate_streak(user) == 1


@pytest.mark.django_db
def test_the_streak_survives_until_a_full_day_is_missed() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 1)
    _log_on(user, action, 2)

    # Nothing today yet, but yesterday still counts: the streak is alive.
    assert recalculate_streak(user) == 2


@pytest.mark.django_db
def test_activity_older_than_yesterday_leaves_no_streak() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 3)

    assert recalculate_streak(user) == 0

    user.profile.refresh_from_db()
    assert user.profile.current_streak == 0


@pytest.mark.django_db
def test_pending_and_rejected_logs_do_not_count() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 0, status=ActionLog.Status.PENDING_AUDIT)
    _log_on(user, action, 1, status=ActionLog.Status.REJECTED)

    assert recalculate_streak(user) == 0


@pytest.mark.django_db
def test_another_users_activity_is_ignored() -> None:
    user = create_student()
    # Reuse the clan: its name carries a unique constraint while it is alive.
    other = User.objects.create_user(email="other@iteso.mx", password="local-only")
    UserProfile.objects.create(
        user=other, institutional_clan=user.profile.institutional_clan
    )
    action = create_bike_action()
    _log_on(other, action, 0)

    assert recalculate_streak(user) == 0


@pytest.mark.django_db
def test_recalculating_twice_gives_the_same_result() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 0)
    _log_on(user, action, 1)

    first = recalculate_streak(user)
    second = recalculate_streak(user)

    assert first == second == 2


@pytest.mark.django_db
def test_a_wrong_stored_value_is_repaired() -> None:
    user = create_student()
    action = create_bike_action()
    _log_on(user, action, 0)
    user.profile.current_streak = 99
    user.profile.save(update_fields=["current_streak"])

    assert recalculate_streak(user) == 1

    user.profile.refresh_from_db()
    assert user.profile.current_streak == 1


@pytest.mark.django_db
def test_logging_an_action_updates_the_streak() -> None:
    user = create_student()
    action = create_bike_action()

    response = post_action_log(user, action)

    assert response.status_code == 201
    user.profile.refresh_from_db()
    assert user.profile.current_streak == 1
    assert user.profile.last_action_date == timezone.localdate()


@pytest.mark.django_db
def test_rejecting_the_only_approved_log_clears_the_streak() -> None:
    user = create_student()
    admin = create_admin()
    action = create_bike_action()
    post_action_log(user, action)
    user.profile.refresh_from_db()
    assert user.profile.current_streak == 1

    log = ActionLog.objects.get(user=user)
    response = audit_action_log(admin, str(log.id), "REJECTED", reason="Evidencia")

    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.current_streak == 0
