"""Read-only queries for the accounts domain."""

from __future__ import annotations

import uuid
from typing import NamedTuple

from django.db.models import QuerySet

from .models import Clan, ClanMembership, User, UserProfile


class ProfileClans(NamedTuple):
    """The profile of a user together with the clans their points credit."""

    profile: UserProfile
    institutional_clan: Clan | None
    active_private_clan: Clan | None


def get_profile_clans(user: User) -> ProfileClans:
    """Return ``user``'s profile and the two clans credited on a points event.

    The institutional clan is a column on UserProfile. The active private clan
    is not: it is the membership flagged ``is_active_private``, which
    ClanMembership restricts to at most one per user, so it is read from there
    instead of being duplicated on the profile.

    Args:
        user: Account whose profile is read.

    Returns:
        The profile plus both clans; either clan is None when unset.

    Raises:
        UserProfile.DoesNotExist: If the account has no profile yet.
    """
    profile = UserProfile.objects.select_related("institutional_clan").get(user=user)
    membership = (
        ClanMembership.objects.select_related("clan")
        .filter(user=user, is_active_private=True)
        .first()
    )
    return ProfileClans(
        profile=profile,
        institutional_clan=profile.institutional_clan,
        active_private_clan=membership.clan if membership else None,
    )


def get_user_by_id(user_id: uuid.UUID) -> User:
    """Return the account identified by ``user_id``."""
    return User.objects.get(pk=user_id)


def list_users() -> QuerySet[User]:
    """Return all accounts, ordered for a stable admin directory listing."""
    return User.objects.order_by("email")
