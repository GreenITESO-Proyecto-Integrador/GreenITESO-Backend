"""Write operations for the clans domain."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.services import ensure_profile


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


def _get_or_create_institutional_clan(career: str) -> Clan:
    """Return the institutional clan for ``career``, creating it on first use.

    Uses ``get_or_create`` rather than a plain check-then-act: ``Clan.name``
    carries a DB-level unique constraint, and concurrent onboarding for the
    same career is plausible (many students in the same career onboarding
    around the same time). ``get_or_create`` retries under a savepoint if the
    initial insert loses the race, matching the pattern used by
    ``ClanMembership.objects.get_or_create`` below.
    """
    clan, created = Clan.objects.get_or_create(
        name=career, defaults={"type": Clan.ClanType.INSTITUTIONAL}
    )
    if not created and clan.type != Clan.ClanType.INSTITUTIONAL:
        raise ValueError(f"'{career}' is already in use by a non-institutional clan.")
    return clan


@transaction.atomic
def assign_institutional_clan(*, user: User, career: str) -> UserProfile:
    """Declare ``career`` and auto-assign its institutional clan (FR T2-30).

    Re-running with the same career is idempotent; re-running with a
    different career reassigns the profile and removes the stale membership
    in the previous institutional clan, so the user always ends up a member
    of exactly one institutional clan.
    """
    career = career.strip()
    if not career:
        raise ValueError("career must not be blank")

    profile = ensure_profile(user)
    clan = _get_or_create_institutional_clan(career)
    previous_clan = profile.institutional_clan

    profile.career = career
    profile.institutional_clan = clan
    if profile.onboarding_completed_at is None:
        profile.onboarding_completed_at = timezone.now()
    profile.save(
        update_fields=["career", "institutional_clan", "onboarding_completed_at"]
    )

    if previous_clan is not None and previous_clan.pk != clan.pk:
        ClanMembership.objects.filter(
            user=user,
            clan=previous_clan,
            role=ClanMembership.MembershipRole.MEMBER,
        ).delete()

    ClanMembership.objects.get_or_create(
        user=user,
        clan=clan,
        defaults={"role": ClanMembership.MembershipRole.MEMBER},
    )
    return profile


def _assert_can_dissolve(*, clan: Clan, actor: User) -> None:
    """Enforce that only the leader of a private clan dissolves it (FR-CLAN-03).

    Must be called with ``clan`` already locked via ``select_for_update`` in
    the caller's transaction, and checks leadership against that locked row's
    current membership state rather than a snapshot taken before the lock.
    """
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

    Locks the ``clan`` row before checking leadership, matching the discipline
    ``assign_leader`` and ``ClanMembershipAdmin.save_model`` already use:
    checking permission on an unlocked read and only then locking would leave
    a window where a concurrent leadership transfer commits in between,
    letting an actor who is no longer LEADER dissolve the clan anyway.
    Locking first means whichever operation gets there first fully commits
    before the other re-reads current state. ``all_objects`` is used for the
    lock fetch (rather than the default ``objects`` manager, which excludes
    soft-deleted rows) so a concurrent double-dissolve can still find the row
    and return it idempotently instead of raising ``Clan.DoesNotExist``.

    Args:
        clan: Clan to dissolve.
        actor: User requesting the dissolution; must be its LEADER.

    Returns:
        The dissolved clan, refreshed from the database.

    Raises:
        PermissionDenied: If the clan is institutional or the actor is not its leader.
    """
    locked = Clan.all_objects.select_for_update().get(pk=clan.pk)
    _assert_can_dissolve(clan=locked, actor=actor)
    if locked.deleted_at is not None:
        # Already dissolved by a concurrent request: keep the original timestamp.
        return locked
    locked.deleted_at = timezone.now()
    locked.save(update_fields=["deleted_at"])
    ClanMembership.objects.filter(clan=locked, is_active_private=True).update(
        is_active_private=False
    )
    return locked
