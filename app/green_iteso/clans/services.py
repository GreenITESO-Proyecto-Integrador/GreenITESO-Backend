"""Write operations for the clans domain."""

from __future__ import annotations

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


@transaction.atomic
def select_active_private_clan(*, user: User, clan: Clan) -> ClanMembership:
    """Set ``clan`` as ``user``'s active private clan for point attribution (BR-03).

    Locks the ``user`` row first so two concurrent selections by the same
    user always serialize: the ``membership_one_active_private_per_user`` DB
    constraint would otherwise only catch the conflict if both requests
    happened to race on the exact same row, not two different ones.
    Clearing any previous active membership and setting the new one inside
    the same transaction keeps exactly one active row per user at all times.

    Raises:
        ValueError: If ``clan`` is not PRIVATE, or ``user`` is not one of
            its members.
    """
    if clan.type != Clan.ClanType.PRIVATE:
        raise ValueError("Only private clans can be selected as active.")
    User.objects.select_for_update().get(pk=user.pk)
    try:
        membership = ClanMembership.objects.get(user=user, clan=clan)
    except ClanMembership.DoesNotExist as exc:
        raise ValueError("User is not a member of this clan.") from exc

    ClanMembership.objects.filter(user=user, is_active_private=True).exclude(
        pk=membership.pk
    ).update(is_active_private=False)
    membership.is_active_private = True
    membership.save(update_fields=["is_active_private"])
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
