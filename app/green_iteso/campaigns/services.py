"""Authorization helpers for campaign management."""

from __future__ import annotations

from typing import Any

from green_iteso.accounts.models import ClanMembership
from green_iteso.core.roles import ClanRole, GlobalRole

from .models import Campaign


def can_manage_campaign(user: Any, campaign: Campaign) -> bool:
    """Return whether the user may manage the campaign's missions.

    Admins manage any campaign; global campaigns are admin-only; private
    campaigns are also managed by the target clan's leader.
    """
    if user.role == GlobalRole.ADMIN:
        return True
    if campaign.scope == Campaign.Scope.GLOBAL:
        return False
    return ClanMembership.objects.filter(
        user=user, clan_id=campaign.target_clan_id, role=ClanRole.LEADER
    ).exists()
