"""Write operations for the clans domain."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.services import ensure_profile

MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER = 5
"""BR-04: a user may belong to at most this many private clans at once."""


class DuplicateClanNameError(ValueError):
    """Raised when ``Clan.name`` is already taken (unique per the T9a schema)."""


class AlreadyLeadingAClanError(ValueError):
    """Raised when BR-04's single-leadership-per-user rule would be violated."""


class PrivateClanLimitExceededError(ValueError):
    """Raised when BR-04's five-private-clan membership limit would be exceeded."""


def _is_already_leading_a_clan(user: User) -> bool:
    """Return whether ``user`` currently leads any non-deleted clan (BR-04)."""
    return ClanMembership.objects.filter(
        user=user,
        role=ClanMembership.MembershipRole.LEADER,
        clan__deleted_at__isnull=True,
    ).exists()


def _count_private_clan_memberships(user: User) -> int:
    """Return how many non-deleted private clans ``user`` currently belongs to."""
    return ClanMembership.objects.filter(
        user=user,
        clan__type=Clan.ClanType.PRIVATE,
        clan__deleted_at__isnull=True,
    ).count()


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
    auto-assigned during onboarding (T2-30, see ``assign_institutional_clan``).
    Enforces BR-04: a user may lead at most one clan at a time, and may belong
    to at most ``MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER`` private clans. The
    creator row is locked for the duration of the check-then-act sequence so
    two concurrent requests from the same user cannot both pass the BR-04
    checks before either membership row exists.
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


def _get_or_create_institutional_clan(career: str) -> Clan:
    """Return the institutional clan for ``career``, creating it on first use.

    The T9a draft does not ship a separate career/department catalog, so the
    career string itself is the clan's stable name (``Clan.name`` is unique).
    If a clan already owns that name but is not institutional, auto-assignment
    is rejected instead of silently repurposing it; resolving that collision
    is a service/product policy, not a decision this helper makes.
    """
    existing = Clan.objects.filter(name=career).first()
    if existing is not None:
        if existing.type != Clan.ClanType.INSTITUTIONAL:
            raise ValueError(
                f"'{career}' is already in use by a non-institutional clan."
            )
        return existing
    return Clan.objects.create(name=career, type=Clan.ClanType.INSTITUTIONAL)


@transaction.atomic
def assign_institutional_clan(*, user: User, career: str) -> UserProfile:
    """Declare ``career`` and auto-assign its institutional clan (FR T2-30).

    Creates the matching institutional clan on first use, freezes it on the
    profile, and grants the caller a MEMBER row in it. Re-running with the
    same career is idempotent; re-running with a different career reassigns
    the profile but never removes the earlier membership history. This stays
    in the clans domain: it only reads/writes ``accounts`` models and the
    existing ``ensure_profile`` helper, and never edits the accounts app.
    """
    career = career.strip()
    if not career:
        raise ValueError("career must not be blank")

    profile = ensure_profile(user)
    clan = _get_or_create_institutional_clan(career)

    profile.career = career
    profile.institutional_clan = clan
    if profile.onboarding_completed_at is None:
        profile.onboarding_completed_at = timezone.now()
    profile.save(
        update_fields=["career", "institutional_clan", "onboarding_completed_at"]
    )

    ClanMembership.objects.get_or_create(
        user=user,
        clan=clan,
        defaults={"role": ClanMembership.MembershipRole.MEMBER},
    )
    return profile
