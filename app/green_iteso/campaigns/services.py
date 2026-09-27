"""Authorization helpers for campaign management."""

from __future__ import annotations

from typing import Any

from green_iteso.accounts.models import Clan, ClanMembership
from green_iteso.core.roles import ClanRole, GlobalRole

from .models import Campaign


def is_clan_leader(user: Any, clan: Clan | None) -> bool:
    """Return whether the user leads the given clan.

    Only live clans count: a missing or soft-deleted clan has no leader.
    """
    if clan is None or clan.deleted_at is not None:
        return False
    return ClanMembership.objects.filter(
        user=user, clan=clan, role=ClanRole.LEADER
    ).exists()


def can_create_campaign(user: Any, scope: str, target_clan: Clan | None) -> bool:
    """Return whether the user may create a campaign with the given scope.

    Global campaigns are admin-only. Private campaigns can only be created by
    the leader of the target clan; being an admin grants no access to them.
    """
    if scope == Campaign.Scope.GLOBAL:
        return user.role == GlobalRole.ADMIN
    if scope == Campaign.Scope.PRIVATE:
        return is_clan_leader(user, target_clan)
    return False


def can_manage_campaign(user: Any, campaign: Campaign) -> bool:
    """Return whether the user may manage the campaign's missions.

    Global campaigns are managed only by admins. Private campaigns are managed
    only by the leader of the target clan; admins have no access to them.
    """
    if campaign.scope == Campaign.Scope.GLOBAL:
        return user.role == GlobalRole.ADMIN
    return is_clan_leader(user, campaign.target_clan)
