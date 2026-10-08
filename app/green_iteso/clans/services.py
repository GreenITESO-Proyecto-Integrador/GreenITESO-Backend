"""Write operations for the clans domain."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
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
            clan=clan,
            role=ClanMembership.MembershipRole.LEADER,
            status=ClanMembership.Status.ACCEPTED,
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
def transfer_leadership(*, clan: Clan, actor: User, successor: User) -> ClanMembership:
    """Transfer ``clan``'s LEADER role from ``actor`` to ``successor`` (FR-CLAN-03).

    Locks the ``clan`` row before checking who currently holds LEADER,
    matching the discipline ``dissolve_clan`` and ``assign_leader`` already
    use: checking on an unlocked read would leave a window where a
    concurrent transfer or dissolve commits in between, letting a caller who
    is no longer LEADER go through anyway. Demoting the current leader and
    delegating the promotion to ``assign_leader`` (rather than setting the
    role directly) keeps a single place enforcing "at most one LEADER per
    clan", including its own re-check under the same lock.

    Also locks the ``successor`` row and re-checks BR-04's single-leadership
    rule on them: without this, a user can be made leader of this clan while
    concurrently becoming leader of another one, ending up leading two
    clans at once. Both the actor's and the successor's memberships must be
    ACCEPTED: a PENDING join request is not membership yet.

    Raises:
        PermissionDenied: If ``actor`` is not the clan's current LEADER.
        ValueError: If ``successor`` is ``actor``, is not an accepted member
            of ``clan``, or already leads another clan.
    """
    locked = Clan.objects.select_for_update().get(pk=clan.pk)
    actor_membership = ClanMembership.objects.filter(
        clan=locked,
        user=actor,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
    ).first()
    if actor_membership is None:
        raise PermissionDenied(
            "Only the clan's current leader can transfer leadership."
        )
    if successor.pk == actor.pk:
        raise ValueError("Cannot transfer leadership to yourself.")
    try:
        successor_membership = ClanMembership.objects.get(
            clan=locked, user=successor, status=ClanMembership.Status.ACCEPTED
        )
    except ClanMembership.DoesNotExist as exc:
        raise ValueError("Successor must be a member of the clan.") from exc

    locked_successor = User.objects.select_for_update().get(pk=successor.pk)
    if _is_already_leading_a_clan(locked_successor):
        raise ValueError("Successor already leads another clan.")

    actor_membership.role = ClanMembership.MembershipRole.MEMBER
    actor_membership.save(update_fields=["role"])
    return assign_leader(clan=locked, membership=successor_membership)


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
        ValueError: If ``clan`` is not PRIVATE, or ``user`` has no accepted
            membership in it.
    """
    if clan.type != Clan.ClanType.PRIVATE:
        raise ValueError("Only private clans can be selected as active.")
    User.objects.select_for_update().get(pk=user.pk)
    try:
        membership = ClanMembership.objects.get(
            user=user, clan=clan, status=ClanMembership.Status.ACCEPTED
        )
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


MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER = 5
"""BR-04: a user may belong to at most this many private clans at once."""


class DuplicateClanNameError(ValueError):
    """Raised when ``Clan.name`` is already taken by another alive clan."""


class AlreadyLeadingAClanError(ValueError):
    """Raised when BR-04's single-leadership-per-user rule would be violated."""


class PrivateClanLimitExceededError(ValueError):
    """Raised when BR-04's five-private-clan membership limit would be exceeded."""


class InvalidClanPrivacyError(ValueError):
    """Raised when ``privacy`` is not one of ``Clan.Privacy``'s values."""


def _is_already_leading_a_clan(user: User) -> bool:
    """Return whether ``user`` currently leads any non-dissolved clan (BR-04).

    Excludes soft-deleted clans: ``dissolve_clan`` intentionally keeps the
    LEADER membership row around for points history, so without this filter
    a user who dissolved their clan would be permanently blocked from ever
    leading another one. Excludes non-ACCEPTED rows too: a PENDING or
    REJECTED row is not real leadership.
    """
    return ClanMembership.objects.filter(
        user=user,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
        clan__deleted_at__isnull=True,
    ).exists()


def _count_private_clan_memberships(user: User) -> int:
    """Return how many non-dissolved private clans ``user`` currently belongs to.

    Excludes soft-deleted clans for the same reason as
    ``_is_already_leading_a_clan``: a dissolved clan's membership rows are
    kept for history and must not keep counting against BR-04's 5-clan cap.
    Excludes non-ACCEPTED rows: a PENDING join request isn't membership yet
    and shouldn't count against, or be blocked by, the cap.
    """
    return ClanMembership.objects.filter(
        user=user,
        clan__type=Clan.ClanType.PRIVATE,
        status=ClanMembership.Status.ACCEPTED,
        clan__deleted_at__isnull=True,
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

    if privacy not in Clan.Privacy.values:
        raise InvalidClanPrivacyError(f"'{privacy}' is not a valid Clan.Privacy value.")

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
        if "clan_name_unique_when_alive" not in str(exc):
            raise
        raise DuplicateClanNameError(f"A clan named '{name}' already exists.") from exc

    ClanMembership.objects.create(
        user=locked_user,
        clan=clan,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
    )
    return clan


@transaction.atomic
def join_clan(*, clan: Clan, user: User) -> ClanMembership:
    """Join a PUBLIC clan directly, or file a pending request for a
    PRIVATE_INVITE one (T2-32).

    PUBLIC clans grant an ACCEPTED membership immediately, enforcing BR-04's
    5-clan cap right away since the user becomes a real member on the spot.
    PRIVATE_INVITE clans only create a PENDING request: BR-04 is deferred to
    ``accept_join_request``, since a pending request isn't membership yet
    and shouldn't count against, or be blocked by, the cap.

    A previously REJECTED row is reused (its status flips back to PENDING or
    ACCEPTED) rather than inserting a new row, since ``membership_user_clan_
    unique`` only allows one ``ClanMembership`` per (user, clan) pair.

    Raises:
        ValueError: If the clan is institutional, or the user already has a
            pending or accepted record for this clan.
        PrivateClanLimitExceededError: If joining a PUBLIC clan would exceed
            BR-04's 5-clan cap.
    """
    if clan.type != Clan.ClanType.PRIVATE:
        raise ValueError("Only private clans can be joined through this action.")

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    existing = ClanMembership.objects.filter(user=locked_user, clan=clan).first()
    if existing is not None and existing.status != ClanMembership.Status.REJECTED:
        state = (
            "a pending request"
            if existing.status == ClanMembership.Status.PENDING
            else "membership"
        )
        raise ValueError(f"User already has {state} for this clan.")

    if clan.privacy == Clan.Privacy.PUBLIC:
        if (
            _count_private_clan_memberships(locked_user)
            >= MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER
        ):
            raise PrivateClanLimitExceededError(
                "User already belongs to "
                f"{MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER} private clans (BR-04)."
            )
        new_status = ClanMembership.Status.ACCEPTED
    else:
        new_status = ClanMembership.Status.PENDING

    if existing is not None:
        existing.status = new_status
        existing.save(update_fields=["status"])
        return existing
    return ClanMembership.objects.create(user=locked_user, clan=clan, status=new_status)


@transaction.atomic
def accept_join_request(*, clan: Clan, actor: User, applicant: User) -> ClanMembership:
    """Accept a PENDING join request for a PRIVATE_INVITE clan (T2-32).

    Only the clan's current LEADER may accept. Locks the ``clan`` row before
    checking leadership, matching ``transfer_leadership``: an unlocked read
    would leave a window where a concurrent transfer commits in between and
    a just-demoted ex-leader still gets through. Enforces BR-04 at acceptance
    time, since this is the moment the applicant actually becomes a member:
    locks the applicant's user row (always after the clan row, the same
    order ``transfer_leadership`` uses) and re-checks the 5-clan cap before
    flipping PENDING to ACCEPTED.

    Raises:
        PermissionDenied: If ``actor`` is not the clan's current LEADER.
        ValueError: If there is no PENDING request from ``applicant``.
        PrivateClanLimitExceededError: If accepting would exceed BR-04's
            5-clan cap.
    """
    locked = Clan.objects.select_for_update().get(pk=clan.pk)
    is_leader = ClanMembership.objects.filter(
        clan=locked,
        user=actor,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
    ).exists()
    if not is_leader:
        raise PermissionDenied("Only the clan's leader can accept join requests.")

    locked_applicant = User.objects.select_for_update().get(pk=applicant.pk)
    try:
        membership = ClanMembership.objects.get(
            clan=locked, user=locked_applicant, status=ClanMembership.Status.PENDING
        )
    except ClanMembership.DoesNotExist as exc:
        raise ValueError("No pending join request from this user.") from exc

    if (
        _count_private_clan_memberships(locked_applicant)
        >= MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER
    ):
        raise PrivateClanLimitExceededError(
            "User already belongs to "
            f"{MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER} private clans (BR-04)."
        )

    membership.status = ClanMembership.Status.ACCEPTED
    membership.save(update_fields=["status"])
    return membership


@transaction.atomic
def reject_join_request(*, clan: Clan, actor: User, applicant: User) -> ClanMembership:
    """Reject a PENDING join request for a PRIVATE_INVITE clan (T2-32).

    Only the clan's current LEADER may reject. Locks the ``clan`` row before
    checking leadership, for the same reason as ``accept_join_request``: a
    concurrent ``transfer_leadership`` must not be able to demote the actor
    between the check and the write. The row is kept as REJECTED rather than
    deleted, matching ``membership_user_clan_unique``: it lets the applicant
    file a new request later (``join_clan`` reuses the row) without a stale
    row blocking the insert.

    Raises:
        PermissionDenied: If ``actor`` is not the clan's current LEADER.
        ValueError: If there is no PENDING request from ``applicant``.
    """
    locked = Clan.objects.select_for_update().get(pk=clan.pk)
    is_leader = ClanMembership.objects.filter(
        clan=locked,
        user=actor,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
    ).exists()
    if not is_leader:
        raise PermissionDenied("Only the clan's leader can reject join requests.")

    try:
        membership = ClanMembership.objects.get(
            clan=locked, user=applicant, status=ClanMembership.Status.PENDING
        )
    except ClanMembership.DoesNotExist as exc:
        raise ValueError("No pending join request from this user.") from exc

    membership.status = ClanMembership.Status.REJECTED
    membership.save(update_fields=["status"])
    return membership


@transaction.atomic
def leave_clan(*, clan: Clan, user: User) -> None:
    """Leave a private clan, removing the caller's own membership (T2-33).

    Locks the ``clan`` row before checking the caller's role, matching the
    discipline ``transfer_leadership`` and ``dissolve_clan`` already use: a
    concurrent transfer committing in between could otherwise let a stale
    "I'm not the leader" read through incorrectly. The LEADER must transfer
    leadership (or dissolve the clan) before leaving, so a clan is never
    left without a leader.

    Institutional clans can't be left: ``join_clan`` refuses them, and their
    membership is assigned from ``profile.institutional_clan`` (BR-04), so
    deleting it would leave the profile pointing at a clan the user no longer
    belongs to.

    Raises:
        PermissionDenied: If the caller is the clan's current LEADER.
        ValueError: If the clan is institutional, or the caller has no
            ACCEPTED membership in this clan.
    """
    if clan.type != Clan.ClanType.PRIVATE:
        raise ValueError("Only private clans can be left through this action.")

    locked = Clan.objects.select_for_update().get(pk=clan.pk)
    try:
        membership = ClanMembership.objects.get(
            clan=locked, user=user, status=ClanMembership.Status.ACCEPTED
        )
    except ClanMembership.DoesNotExist as exc:
        raise ValueError("User is not a member of this clan.") from exc

    if membership.role == ClanMembership.MembershipRole.LEADER:
        raise PermissionDenied(
            "The clan's leader must transfer leadership or dissolve the clan "
            "before leaving."
        )
    membership.delete()


def _assert_can_dissolve(*, clan: Clan, actor: User) -> None:
    """Enforce that only the leader of a private clan dissolves it (FR-CLAN-03).

    Must be called with ``clan`` already locked via ``select_for_update`` in
    the caller's transaction, and checks leadership against that locked row's
    current membership state rather than a snapshot taken before the lock.
    """
    if clan.type != Clan.ClanType.PRIVATE:
        raise PermissionDenied("Only private clans can be dissolved.")
    is_leader = ClanMembership.objects.filter(
        clan=clan,
        user=actor,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
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
