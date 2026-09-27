"""Authorization helpers for campaign management."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from green_iteso.accounts.models import Clan, ClanMembership
from green_iteso.core.roles import ClanRole, GlobalRole

from .models import Campaign, Mission


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
        return is_clan_leader(user, target_clan) or _is_admin_for_institutional_clan(
            user, target_clan
        )
    return False


def can_manage_campaign(user: Any, campaign: Campaign) -> bool:
    """Return whether the user may manage the campaign's missions.

    Global campaigns are managed only by admins. Private campaigns are
    managed by the leader of the target clan, or by an admin when the target
    clan is institutional; admins have no manage access to non-institutional
    (private/friend) clans' campaigns.
    """
    if campaign.scope == Campaign.Scope.GLOBAL:
        return user.role == GlobalRole.ADMIN
    return is_clan_leader(
        user, campaign.target_clan
    ) or _is_admin_for_institutional_clan(user, campaign.target_clan)


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
