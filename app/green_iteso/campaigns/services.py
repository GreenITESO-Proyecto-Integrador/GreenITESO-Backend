"""Authorization helpers for campaign management."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from green_iteso.accounts.models import Clan, ClanMembership
from green_iteso.actions.models import ActionLog, ActionLogMissionContribution
from green_iteso.core.roles import ClanRole, GlobalRole

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress


def is_clan_leader(user: Any, clan: Clan | None) -> bool:
    """Return whether the user leads the given clan.

    Only live clans count: a missing or soft-deleted clan has no leader.
    """
    if clan is None or clan.deleted_at is not None:
        return False
    return ClanMembership.objects.filter(
        user=user, clan=clan, role=ClanRole.LEADER
    ).exists()


def _is_admin_for_institutional_clan(user: Any, clan: Clan | None) -> bool:
    """Return whether the user is an admin managing an institutional clan.

    Admins may create and manage private campaigns targeted at institutional
    clans, alongside that clan's leader. They still have no access to
    campaigns targeting non-institutional (private/friend) clans.
    """
    if clan is None or clan.deleted_at is not None:
        return False
    return user.role == GlobalRole.ADMIN and clan.type == Clan.ClanType.INSTITUTIONAL


def can_create_campaign(user: Any, scope: str, target_clan: Clan | None) -> bool:
    """Return whether the user may create a campaign with the given scope.

    Global campaigns are admin-only. Private campaigns can be created by the
    leader of the target clan, or by an admin when the target clan is
    institutional; admins have no create access to non-institutional
    (private/friend) clans' campaigns.
    """
    if scope == Campaign.Scope.GLOBAL:
        return user.role == GlobalRole.ADMIN
    if scope == Campaign.Scope.PRIVATE:
        if target_clan is not None and target_clan.type == Clan.ClanType.INSTITUTIONAL:
            return _is_admin_for_institutional_clan(user, target_clan)
        return is_clan_leader(user, target_clan)
    return False


def _resolve_can_manage(
    user: Any, campaign: Campaign, leader_clan_ids: set[Any] | None = None
) -> bool:
    """Return whether the user may manage the campaign's missions.

    Global campaigns are managed only by admins. Private campaigns targeting
    an institutional clan are admin-only; other private campaigns are
    managed by the leader of the target clan. When ``leader_clan_ids`` is
    given, it is used instead of querying ``ClanMembership`` for leadership,
    letting callers precompute it once for a batch of campaigns.
    """
    if campaign.scope == Campaign.Scope.GLOBAL:
        return user.role == GlobalRole.ADMIN
    target_clan = campaign.target_clan
    if target_clan is not None and target_clan.type == Clan.ClanType.INSTITUTIONAL:
        return _is_admin_for_institutional_clan(user, target_clan)
    if leader_clan_ids is not None:
        return target_clan is not None and target_clan.pk in leader_clan_ids
    return is_clan_leader(user, target_clan)


def can_manage_campaign(user: Any, campaign: Campaign) -> bool:
    """Return whether the user may manage the campaign's missions."""
    return _resolve_can_manage(user, campaign)


def can_manage_campaign_for_user(
    user: Any, campaign: Campaign, leader_clan_ids: set[Any] | None = None
) -> bool:
    """Return whether an (possibly unauthenticated) user may manage a campaign.

    Unlike :func:`can_manage_campaign`, this tolerates an unauthenticated or
    ``None`` user, returning ``False`` for it. ``leader_clan_ids`` lets a
    caller reuse a single precomputed set of clans the user leads across
    many campaigns instead of a per-campaign membership query.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return _resolve_can_manage(user, campaign, leader_clan_ids)


def compute_campaign_status(
    start_date: datetime, end_date: datetime, now: datetime
) -> str:
    """Return the lifecycle status a campaign has at ``now``."""
    if now < start_date:
        return Campaign.Status.PROMOTION
    if now < end_date:
        return Campaign.Status.IN_PROGRESS
    return Campaign.Status.FINISHED


def sync_campaign_statuses(now: datetime | None = None) -> int:
    """Advance stored campaign statuses to match their dates.

    Statuses only move forward and FINISHED is terminal. The function is
    idempotent and safe under concurrent runs: each bulk UPDATE filters on the
    current status, so a row already advanced by another run is not matched
    again. Pending proposals are excluded: their status only starts tracking
    dates once an admin approves them. Returns the total number of rows
    updated.
    """
    if now is None:
        now = timezone.now()
    finished = Campaign.objects.filter(
        status__in=[Campaign.Status.PROMOTION, Campaign.Status.IN_PROGRESS],
        approval_status=Campaign.ApprovalStatus.APPROVED,
        end_date__lte=now,
    ).update(status=Campaign.Status.FINISHED)
    started = Campaign.objects.filter(
        status=Campaign.Status.PROMOTION,
        approval_status=Campaign.ApprovalStatus.APPROVED,
        start_date__lte=now,
        end_date__gt=now,
    ).update(status=Campaign.Status.IN_PROGRESS)
    return finished + started


def create_campaign_with_missions(
    campaign_data: dict[str, Any], missions_data: list[dict[str, Any]]
) -> Campaign:
    """Create a campaign and its nested missions atomically."""
    with transaction.atomic():
        campaign = Campaign.objects.create(**campaign_data)
        for mission_data in missions_data:
            Mission.objects.create(campaign=campaign, **mission_data)
    return campaign


def propose_global_campaign(user: Any, validated_data: dict[str, Any]) -> Campaign:
    """Create a pending global campaign proposal on behalf of ``user``."""
    validated_data = dict(validated_data)
    missions_data = validated_data.pop("missions", [])
    campaign_data = {
        **validated_data,
        "scope": Campaign.Scope.GLOBAL,
        "status": Campaign.Status.PROMOTION,
        "approval_status": Campaign.ApprovalStatus.PENDING,
        "creator": user,
    }
    return create_campaign_with_missions(campaign_data, missions_data)


def approve_campaign(admin: Any, campaign_id: UUID) -> Campaign:
    """Approve a pending global campaign proposal."""
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.approval_status != Campaign.ApprovalStatus.PENDING:
            raise ValidationError("Only pending proposals can be reviewed.")
        now = timezone.now()
        if campaign.end_date <= now:
            raise ValidationError(
                "Cannot approve a proposal whose end date has passed."
            )
        campaign.approval_status = Campaign.ApprovalStatus.APPROVED
        campaign.reviewed_by = admin
        campaign.reviewed_at = now
        campaign.status = compute_campaign_status(
            campaign.start_date, campaign.end_date, now
        )
        campaign.save(
            update_fields=["approval_status", "reviewed_by", "reviewed_at", "status"]
        )
    return campaign


def reject_campaign(admin: Any, campaign_id: UUID, reason: str) -> Campaign:
    """Reject a pending global campaign proposal with a required reason."""
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.approval_status != Campaign.ApprovalStatus.PENDING:
            raise ValidationError("Only pending proposals can be reviewed.")
        if not reason:
            raise ValidationError({"rejection_reason": "Rejection reason is required."})
        campaign.approval_status = Campaign.ApprovalStatus.REJECTED
        campaign.reviewed_by = admin
        campaign.reviewed_at = timezone.now()
        campaign.rejection_reason = reason
        campaign.save(
            update_fields=[
                "approval_status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
            ]
        )
    return campaign


def validate_action_log_campaign(
    user: Any, action: Any, campaign_id: UUID | None
) -> Campaign | None:
    """Validate that ``user`` may credit ``action`` toward ``campaign_id``.

    Used by Team 1 before creating an ActionLog (BR-07, BR-03). Returns the
    validated campaign, or None when ``campaign_id`` is None.
    """
    if campaign_id is None:
        return None
    try:
        campaign = Campaign.objects.get(
            pk=campaign_id, approval_status=Campaign.ApprovalStatus.APPROVED
        )
    except Campaign.DoesNotExist as error:
        raise ValidationError(
            {"campaign_id": "Campaign does not exist or is not approved."}
        ) from error

    sync_campaign_statuses()
    campaign.refresh_from_db()
    if campaign.status != Campaign.Status.IN_PROGRESS:
        raise ValidationError({"campaign_id": "Campaign is not in progress."})
    if not CampaignParticipant.objects.filter(campaign=campaign, user=user).exists():
        raise ValidationError(
            {"campaign_id": "User is not a participant of this campaign."}
        )
    if not Mission.objects.filter(campaign=campaign, action=action).exists():
        raise ValidationError(
            {"campaign_id": "Campaign has no mission for this action."}
        )
    return campaign


def recalculate_mission_progress(user: Any, mission: Mission) -> UserMissionProgress:
    """Recompute and persist ``user``'s progress toward ``mission``.

    Counts non-rejected ActionLogMissionContribution rows, clamped to the
    mission target, and saves only when the stored value changed.
    """
    with transaction.atomic():
        try:
            progress, _ = UserMissionProgress.objects.get_or_create(
                user=user, mission=mission
            )
        except IntegrityError:
            progress = UserMissionProgress.objects.get(user=user, mission=mission)
        progress = UserMissionProgress.objects.select_for_update().get(pk=progress.pk)

        valid = (
            ActionLogMissionContribution.objects.filter(
                mission=mission, action_log__user=user
            )
            .filter(action_log__status=ActionLog.Status.APPROVED)
            .count()
        )
        current_count = min(valid, mission.target_count)
        is_completed = current_count >= mission.target_count

        if (
            progress.current_count != current_count
            or progress.is_completed != is_completed
        ):
            progress.current_count = current_count
            progress.is_completed = is_completed
            progress.save(update_fields=["current_count", "is_completed"])
    return progress


def apply_action_log_to_missions(action_log: Any) -> list[UserMissionProgress]:
    """Credit ``action_log`` toward every eligible mission's progress.

    Precondition: must be called inside the caller's transaction, at the same
    point the log's points are credited. Does nothing if the log is REJECTED.
    """
    if action_log.status == action_log.Status.REJECTED:
        return []

    sync_campaign_statuses()
    missions = Mission.objects.filter(
        action=action_log.action,
        campaign__approval_status=Campaign.ApprovalStatus.APPROVED,
        campaign__status=Campaign.Status.IN_PROGRESS,
        campaign__participants__user=action_log.user,
    ).distinct()

    updated_progress = []
    for mission in missions:
        ActionLogMissionContribution.objects.get_or_create(
            action_log=action_log, mission=mission
        )
        updated_progress.append(recalculate_mission_progress(action_log.user, mission))
    return updated_progress


def revert_action_log_from_missions(action_log: Any) -> list[UserMissionProgress]:
    """Recompute progress for missions ``action_log`` contributed to.

    Precondition: call after saving ``action_log.status = REJECTED``, inside
    the same transaction. Contributions are kept as history; podium snapshots
    are untouched. Applies even if the campaign has since finished.
    """
    contributions = ActionLogMissionContribution.objects.filter(
        action_log=action_log
    ).select_related("mission")
    return [
        recalculate_mission_progress(action_log.user, contribution.mission)
        for contribution in contributions
    ]
