"""DRF selectors for the actions domain."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.utils import timezone

from .models import ActionCategory, ActionLog, ActionMaster

ACTION_LIMIT_TIME_ZONE = ZoneInfo("America/Mexico_City")


def local_day_utc_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return aware UTC bounds for the current Mexico City calendar day."""
    current = timezone.localtime(now or timezone.now(), ACTION_LIMIT_TIME_ZONE)
    local_day = current.date()
    start = datetime.combine(local_day, time.min, tzinfo=ACTION_LIMIT_TIME_ZONE)
    end = datetime.combine(
        local_day + timedelta(days=1), time.min, tzinfo=ACTION_LIMIT_TIME_ZONE
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def count_user_action_logs_for_local_day(
    user_id: UUID, action_id: UUID, *, now: datetime | None = None
) -> int:
    """Count submitted logs for one action in the current local calendar day."""
    start, end = local_day_utc_bounds(now)
    return ActionLog.objects.filter(
        user_id=user_id,
        action_id=action_id,
        created_at__gte=start,
        created_at__lt=end,
    ).count()


def list_active_action_categories() -> QuerySet[ActionCategory]:
    """Return action categories ordered by code."""
    return ActionCategory.objects.all().order_by("code")


def list_active_actions() -> QuerySet[ActionMaster]:
    """Return active actions ordered by code."""
    return ActionMaster.objects.filter(is_active=True).order_by("code")
