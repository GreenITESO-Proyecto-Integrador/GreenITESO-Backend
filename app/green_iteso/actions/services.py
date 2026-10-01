"""Write operations for the actions domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import F

from green_iteso.accounts.models import Clan, UserProfile
from green_iteso.campaigns.services import (
    advance_missions_for_action,
    rewind_missions_for_action,
)

from .models import ActionLog, ActionLogMissionContribution


class PointsAlreadySpentError(Exception):
    """The user no longer has enough available points to revoke an award."""


@transaction.atomic
def notify_mission_progress(
    action_log: ActionLog,
) -> list[ActionLogMissionContribution]:
    """Tell the missions system that an approved action may advance missions.

    Delegates the progress update to the campaigns domain and records one
    ``ActionLogMissionContribution`` per mission it advanced, so each increment
    stays traceable to the log that caused it. Logs that are not approved do
    not advance missions.

    Args:
        action_log: The action log whose approval should be propagated.

    Returns:
        The contribution rows created for this log.
    """
    if action_log.status != ActionLog.Status.APPROVED:
        return []

    missions = advance_missions_for_action(
        user=action_log.user,
        action=action_log.action,
        occurred_at=action_log.created_at,
        campaign=action_log.campaign,
    )
    return ActionLogMissionContribution.objects.bulk_create(
        ActionLogMissionContribution(action_log=action_log, mission=mission)
        for mission in missions
    )


@transaction.atomic
def revert_mission_progress(action_log: ActionLog) -> int:
    """Undo the mission increments a log caused when its evidence is rejected.

    Each ``ActionLogMissionContribution`` records exactly one increment, so the
    campaigns domain rewinds one step per contributed mission. The contribution
    rows are then removed because they no longer count toward any mission.

    Args:
        action_log: The log being rejected.

    Returns:
        How many mission increments were reverted.
    """
    contributions = action_log.mission_contributions.select_related("mission")
    missions = [contribution.mission for contribution in contributions]
    if not missions:
        return 0

    rewind_missions_for_action(user=action_log.user, missions=missions)
    contributions.delete()
    return len(missions)


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
