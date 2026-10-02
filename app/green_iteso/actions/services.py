"""Write rules for registering an action log."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from green_iteso.accounts.models import User

from .models import ActionLog, ActionMaster


class DailyActionLimitError(Exception):
    """The user already used this action's quota for the local calendar day."""


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
