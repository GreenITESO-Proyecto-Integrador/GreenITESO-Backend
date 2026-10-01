"""Write operations for the actions domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import F

from green_iteso.accounts.models import Clan, UserProfile
from green_iteso.campaigns.models import UserMissionProgress
from green_iteso.campaigns.services import (
    apply_action_log_to_missions,
    revert_action_log_from_missions,
)

from .models import ActionLog


class PointsAlreadySpentError(Exception):
    """The user no longer has enough available points to revoke an award."""


def notify_mission_progress(action_log: ActionLog) -> list[UserMissionProgress]:
    """Tell the missions system that an approved action may advance missions.

    The campaigns domain only skips REJECTED logs, so pending logs are
    filtered here: missions advance at the same point points are credited.

    Args:
        action_log: The action log whose approval should be propagated.

    Returns:
        The mission progress rows the campaigns domain recalculated.
    """
    if action_log.status != ActionLog.Status.APPROVED:
        return []
    return apply_action_log_to_missions(action_log)


def revert_mission_progress(action_log: ActionLog) -> list[UserMissionProgress]:
    """Recalculate the missions a rejected log had contributed to.

    The campaigns domain requires the REJECTED status to be saved first; its
    contribution rows are kept as history and simply stop counting.

    Args:
        action_log: The log whose rejection was already saved.

    Returns:
        The mission progress rows the campaigns domain recalculated.
    """
    if action_log.status != ActionLog.Status.REJECTED:
        return []
    return revert_action_log_from_missions(action_log)


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
    Clan.all_objects.filter(pk__in=clan_ids).update(
        total_points=F("total_points") - points
    )
