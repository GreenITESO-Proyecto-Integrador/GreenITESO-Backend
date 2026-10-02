"""Write operations for the actions domain."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from green_iteso.accounts.models import Clan, User, UserProfile

from .models import ActionLog, ActionMaster


class DailyActionLimitError(Exception):
    """The user already used this action's quota for the local calendar day."""


class PointsAlreadySpentError(Exception):
    """The user no longer has enough available points to revoke an award."""


def enforce_calendar_daily_limit(user: User, action: ActionMaster) -> None:
    """Block a new log when non-rejected uses on today's local date reach the quota.

    The day is the calendar date in ``TIME_ZONE`` (``America/Mexico_City``),
    from local midnight inclusive until the next midnight. Call this inside
    ``transaction.atomic``, before inserting the log. The user row is locked so
    two concurrent registrations cannot both pass the count. Rejected logs do
    not consume the quota. Pending audits do, so photo evidence cannot bypass it.
    """
    User.objects.select_for_update().get(pk=user.pk)
    day_start, day_end = _local_day_bounds(timezone.now())
    recent_count = (
        ActionLog.objects.filter(
            user_id=user.pk,
            action=action,
            created_at__gte=day_start,
            created_at__lt=day_end,
        )
        .exclude(status=ActionLog.Status.REJECTED)
        .count()
    )
    if recent_count >= action.daily_limit:
        raise DailyActionLimitError


def _local_day_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """Return ``[local midnight, next local midnight)`` for ``moment``."""
    local = timezone.localtime(moment)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, day_start + timedelta(days=1)


@transaction.atomic
def revoke_awarded_points(action_log: ActionLog) -> None:
    """Deduct exactly the points an approved log credited when it is rejected.

    Uses the frozen ``points_awarded`` and the clans recorded on the log, not
    the current catalog value or the user's current clans, so the deduction
    mirrors the original credit.

    Args:
        action_log: The approved log being rejected.

    Raises:
        PointsAlreadySpentError: The user's available balance is lower than the
            points to deduct, so revoking would leave it negative.
    """
    points = action_log.points_awarded
    profile = UserProfile.objects.select_for_update().get(user_id=action_log.user_id)
    if profile.available_points < points:
        raise PointsAlreadySpentError

    UserProfile.objects.filter(pk=profile.pk).update(
        total_points=F("total_points") - points,
        available_points=F("available_points") - points,
    )
    clan_ids = [
        clan_id
        for clan_id in (
            action_log.institutional_clan_id,
            action_log.credited_private_clan_id,
        )
        if clan_id is not None
    ]
    # all_objects: a dissolved clan keeps its historical total, so it is
    # debited too.
    # Match credit/seed lock order: institutional clan before private clan.
    # A bulk UPDATE may visit these rows in the opposite database-plan order.
    for clan_id in clan_ids:
        Clan.all_objects.filter(pk=clan_id).update(
            total_points=F("total_points") - points
        )
