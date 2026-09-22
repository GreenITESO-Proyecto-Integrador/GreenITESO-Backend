"""Write operations for the clans domain."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User


@transaction.atomic
def create_clan(
    *, name: str, clan_type: str, created_by: User, description: str = ""
) -> Clan:
    """Create a clan and grant its creator the LEADER membership (FR-CLAN-02)."""
    clan = Clan.objects.create(
        name=name,
        type=clan_type,
        description=description,
        created_by=created_by,
    )
    ClanMembership.objects.create(
        user=created_by, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    return clan


def _assert_can_dissolve(*, clan: Clan, actor: User) -> None:
    """Enforce that only the leader of a private clan dissolves it (FR-CLAN-03)."""
    if clan.type != Clan.ClanType.PRIVATE:
        raise PermissionDenied("Only private clans can be dissolved.")
    is_leader = ClanMembership.objects.filter(
        clan=clan, user=actor, role=ClanMembership.MembershipRole.LEADER
    ).exists()
    if not is_leader:
        raise PermissionDenied("Only the clan leader can dissolve the clan.")


@transaction.atomic
def dissolve_clan(*, clan: Clan, actor: User) -> Clan:
    """Dissolve a private clan through a soft delete, preserving history (BR-09).

    Memberships and ``total_points`` are kept so the points contributed by former
    members stay consistent in the global scoreboards. The active private clan
    selection is cleared instead, so future actions no longer credit the clan.

    Args:
        clan: Clan to dissolve.
        actor: User requesting the dissolution; must be its LEADER.

    Returns:
        The dissolved clan, refreshed from the database.

    Raises:
        PermissionDenied: If the clan is institutional or the actor is not its leader.
    """
    _assert_can_dissolve(clan=clan, actor=actor)
    locked = Clan.objects.select_for_update().get(pk=clan.pk)
    if locked.deleted_at is not None:
        # Already dissolved by a concurrent request: keep the original timestamp.
        return locked
    locked.deleted_at = timezone.now()
    locked.save(update_fields=["deleted_at"])
    ClanMembership.objects.filter(clan=locked, is_active_private=True).update(
        is_active_private=False
    )
    return locked
