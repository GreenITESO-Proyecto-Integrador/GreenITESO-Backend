"""Write operations published by the campaigns domain for other apps."""

from __future__ import annotations

from datetime import datetime

from django.db import transaction

from green_iteso.accounts.models import User
from green_iteso.actions.models import ActionMaster

from .models import Campaign, Mission, UserMissionProgress


def _eligible_missions(
    *,
    user: User,
    action: ActionMaster,
    occurred_at: datetime,
    campaign: Campaign | None,
) -> list[Mission]:
    """Return missions for ``action`` in open campaigns the user had joined."""
    missions = Mission.objects.filter(
        action=action,
        campaign__start_date__lte=occurred_at,
        campaign__end_date__gte=occurred_at,
        campaign__participants__user=user,
        campaign__participants__joined_at__lte=occurred_at,
    ).exclude(campaign__status=Campaign.Status.FINISHED)
    if campaign is not None:
        missions = missions.filter(campaign=campaign)
    return list(missions.order_by("id"))


@transaction.atomic
def advance_missions_for_action(
    *,
    user: User,
    action: ActionMaster,
    occurred_at: datetime,
    campaign: Campaign | None = None,
) -> list[Mission]:
    """Increment the user's progress on every mission advanced by one action.

    A mission is eligible when it targets ``action``, its campaign is not
    finished, the action happened inside the campaign window and the user had
    already joined the campaign. When ``campaign`` is given, only its missions
    are considered. Completed missions are left untouched and progress never
    exceeds ``Mission.target_count``.

    Args:
        user: User who performed the approved action.
        action: Catalog action that was performed.
        occurred_at: When the action was logged.
        campaign: Optional campaign the action was explicitly logged for.

    Returns:
        The missions whose progress was incremented.
    """
    advanced: list[Mission] = []
    for mission in _eligible_missions(
        user=user, action=action, occurred_at=occurred_at, campaign=campaign
    ):
        progress, _ = UserMissionProgress.objects.select_for_update().get_or_create(
            user=user, mission=mission
        )
        if progress.is_completed:
            continue
        progress.current_count = min(progress.current_count + 1, mission.target_count)
        progress.is_completed = progress.current_count >= mission.target_count
        progress.save(update_fields=["current_count", "is_completed", "updated_at"])
        advanced.append(mission)
    return advanced
