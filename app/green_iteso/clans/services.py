"""Write operations for the clans domain."""

from __future__ import annotations

from django.db import IntegrityError, transaction
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


MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER = 5
"""BR-04: a user may belong to at most this many private clans at once."""


class DuplicateClanNameError(ValueError):
    """Raised when ``Clan.name`` is already taken by another alive clan."""


class AlreadyLeadingAClanError(ValueError):
    """Raised when BR-04's single-leadership-per-user rule would be violated."""


class PrivateClanLimitExceededError(ValueError):
    """Raised when BR-04's five-private-clan membership limit would be exceeded."""


class NotAClanMemberError(ValueError):
    """Raised when the user has no membership in the target clan."""


class NotAPrivateClanError(ValueError):
    """Raised when trying to select a non-private clan as the active private clan."""


def _is_already_leading_a_clan(user: User) -> bool:
    """Return whether ``user`` currently leads any clan (BR-04)."""
    return ClanMembership.objects.filter(
        user=user, role=ClanMembership.MembershipRole.LEADER
    ).exists()


def _count_private_clan_memberships(user: User) -> int:
    """Return how many private clans ``user`` currently belongs to."""
    return ClanMembership.objects.filter(
        user=user, clan__type=Clan.ClanType.PRIVATE
    ).count()


@transaction.atomic
def create_private_clan(
    *,
    name: str,
    created_by: User,
    description: str = "",
    avatar_object_key: str = "",
    privacy: str = Clan.Privacy.PUBLIC,
) -> Clan:
    """Create a private clan and grant its creator the LEADER membership (T2-31).

    Institutional clans are never created through this path; they are
    auto-assigned during onboarding (see ``assign_institutional_clan``).
    Enforces BR-04: a user may lead at most one clan at a time, and may
    belong to at most ``MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER`` private
    clans. The creator row is locked for the duration of the check-then-act
    sequence so two concurrent requests from the same user cannot both pass
    the BR-04 checks before either membership row exists.
    """
    locked_user = User.objects.select_for_update().get(pk=created_by.pk)

    if _is_already_leading_a_clan(locked_user):
        raise AlreadyLeadingAClanError(
            "User already leads a clan; BR-04 allows a single leadership at a time."
        )
    if (
        _count_private_clan_memberships(locked_user)
        >= MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER
    ):
        raise PrivateClanLimitExceededError(
            "User already belongs to "
            f"{MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER} private clans (BR-04)."
        )

    try:
        clan = Clan.objects.create(
            name=name,
            type=Clan.ClanType.PRIVATE,
            description=description,
            avatar_object_key=avatar_object_key,
            privacy=privacy,
            created_by=locked_user,
        )
    except IntegrityError as exc:
        raise DuplicateClanNameError(f"A clan named '{name}' already exists.") from exc

    ClanMembership.objects.create(
        user=locked_user, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    return clan


@transaction.atomic
def select_active_private_clan(*, user: User, clan: Clan) -> ClanMembership:
    """Mark ``clan`` as the caller's single active private clan (T2-35).

    Only one membership per user may have ``is_active_private=True`` at a
    time (enforced by the ``membership_one_active_private_per_user``
    partial-unique constraint). The user row is locked for the duration of
    the check-then-act sequence, mirroring ``create_private_clan``, so two
    concurrent selections by the same user can't both clear-then-set and
    briefly leave two (or zero) active rows.
    """
    locked_user = User.objects.select_for_update().get(pk=user.pk)

    if clan.type != Clan.ClanType.PRIVATE:
        raise NotAPrivateClanError("Only private clans can be selected as active.")

    try:
        membership = ClanMembership.objects.get(user=locked_user, clan=clan)
    except ClanMembership.DoesNotExist as exc:
        raise NotAClanMemberError("User is not a member of this clan.") from exc

    ClanMembership.objects.filter(user=locked_user, is_active_private=True).exclude(
        pk=membership.pk
    ).update(is_active_private=False)

    if not membership.is_active_private:
        membership.is_active_private = True
        membership.save(update_fields=["is_active_private"])

    return membership
