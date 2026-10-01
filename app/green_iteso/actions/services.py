"""Write operations for the actions domain."""

from __future__ import annotations

from django.db import transaction

from green_iteso.campaigns.services import advance_missions_for_action

from .models import ActionLog, ActionLogMissionContribution


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
