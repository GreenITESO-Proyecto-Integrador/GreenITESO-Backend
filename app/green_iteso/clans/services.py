"""Write operations for the clans domain."""

from __future__ import annotations

from django.db import transaction

from green_iteso.accounts.models import Clan, ClanMembership, User


class DuplicateClanLeaderError(Exception):
    """Raised when a clan would end up with more than one LEADER membership."""


class MembershipClanMismatchError(Exception):
    """Raised when a membership does not belong to the clan it is promoted in."""


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

    Locks the ``clan`` row itself with ``select_for_update`` so two concurrent
    calls for the same clan always serialize, including through the
    zero-leader intermediate state of a leadership transfer. Locking only the
    existing LEADER rows (the previous approach) misses that state: with no
    LEADER row to lock, two concurrent callers both see "no leader" and both
    proceed, producing two LEADER rows.

    Raises:
        MembershipClanMismatchError: If ``membership`` does not belong to
            ``clan``.
        DuplicateClanLeaderError: If another active LEADER membership already
            exists for the clan.
    """
    if membership.clan_id != clan.pk:
        raise MembershipClanMismatchError(
            f"Membership {membership.pk} belongs to clan {membership.clan_id}, "
            f"not {clan.pk}."
        )
    Clan.objects.select_for_update().get(pk=clan.pk)
    existing_leader = (
        ClanMembership.objects.filter(
            clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
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
