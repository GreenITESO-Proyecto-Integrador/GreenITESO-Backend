"""Write operations for the clans domain."""

from __future__ import annotations

from django.db import transaction

from green_iteso.accounts.models import Clan, ClanMembership, User


class DuplicateClanLeaderError(Exception):
    """Raised when a clan would end up with more than one LEADER membership."""


@transaction.atomic
def create_clan(
    *, name: str, clan_type: str, created_by: User, description: str = ""
) -> Clan:
    """Create a clan and grant its creator the LEADER membership (FR-CLAN-02).

    A brand-new clan has no prior memberships, so this always produces exactly
    one LEADER row by construction; the ``assign_leader`` guard below is for
    any later promote/transfer-leadership flow.
    """
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


@transaction.atomic
def assign_leader(*, clan: Clan, membership: ClanMembership) -> ClanMembership:
    """Promote ``membership`` to LEADER, guarding the single-leader-per-clan rule.

    Locks existing LEADER rows for ``clan`` with ``select_for_update`` inside
    the transaction so concurrent promotions cannot race past this check.

    Raises:
        DuplicateClanLeaderError: If another active LEADER membership already
            exists for the clan.
    """
    existing_leader = (
        ClanMembership.objects.select_for_update()
        .filter(clan=clan, role=ClanMembership.MembershipRole.LEADER)
        .exclude(pk=membership.pk)
        .first()
    )
    if existing_leader is not None:
        raise DuplicateClanLeaderError(
            f"Clan {clan.pk} already has LEADER membership {existing_leader.pk}."
        )
    membership.role = ClanMembership.MembershipRole.LEADER
    membership.save(update_fields=["role"])
    return membership
