"""Admin moderation of accounts: banning and unbanning users.

A ban is an account an admin switched off: ``is_active`` is false and
``deactivated_at`` is empty. That is how the login tells it apart from an
account the user deactivated themselves (#176), which comes back by signing in
again: a banned one never does, until an admin unbans it. Nothing of the
person is erased, so an unban gives them back exactly what they had.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.db.models import Q, QuerySet
from rest_framework import status
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from green_iteso.notifications.services import close_user_sockets

from .exceptions import LoginError
from .models import User

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "ACTIVE"
STATUS_BANNED = "BANNED"
STATUS_SELF_DEACTIVATED = "SELF_DEACTIVATED"

FILTER_BANNED = "banned"
FILTER_NOT_BANNED = "not_banned"
STATUS_FILTERS = (FILTER_BANNED, FILTER_NOT_BANNED)

_BANNED_Q = Q(is_active=False, deactivated_at__isnull=True)


class ModerationError(LoginError):
    """A ban or unban the rules do not allow; the message says why."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "VALIDATION_ERROR"
    message = "This action is not allowed."


def account_status(user: User) -> str:
    """``ACTIVE``, ``BANNED`` (by an admin) or ``SELF_DEACTIVATED`` (by the user)."""
    if user.is_active:
        return STATUS_ACTIVE
    return STATUS_BANNED if user.deactivated_at is None else STATUS_SELF_DEACTIVATED


def filter_users(
    users: QuerySet[User], *, status_filter: str | None, search: str | None
) -> QuerySet[User]:
    """Narrow the admin directory by ban status and by id or text.

    ``search`` is an exact match when it is a user id (the one shown with a
    campaign proposal) and a case-insensitive match on email, names or
    nickname otherwise.
    """
    if status_filter == FILTER_BANNED:
        users = users.filter(_BANNED_Q)
    elif status_filter == FILTER_NOT_BANNED:
        users = users.exclude(_BANNED_Q)

    term = (search or "").strip()
    if not term:
        return users
    try:
        return users.filter(pk=uuid.UUID(term))
    except ValueError:
        return users.filter(
            Q(email__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(nickname__icontains=term)
        )


@transaction.atomic
def ban_user(*, admin_user: User, user: User) -> User:
    """Switch ``user`` off: they cannot sign in and their open sessions end.

    Refresh tokens are blacklisted and ``is_active`` cuts off access tokens at
    once; open sockets close after the commit. Progress, clan membership and
    posts are left as they are, so an unban restores the account untouched. A
    self-deactivated account can be banned too, so it can no longer come back.

    Raises:
        ModerationError: If ``user`` is the admin themselves, is an admin (change
            their role first) or is already banned.
    """
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.pk == admin_user.pk:
        raise ModerationError("You cannot ban your own account.")
    if locked.role == User.Role.ADMIN:
        raise ModerationError(
            "Administrators cannot be banned. Change their role first."
        )
    if account_status(locked) == STATUS_BANNED:
        raise ModerationError("This account is already banned.")

    locked.is_active = False
    locked.deactivated_at = None
    locked.save(update_fields=["is_active", "deactivated_at"])

    for token in OutstandingToken.objects.filter(user=locked):
        BlacklistedToken.objects.get_or_create(token=token)

    banned_user_id = locked.pk
    transaction.on_commit(lambda: close_user_sockets(banned_user_id))

    logger.info("Account banned: user_id=%s by=%s", locked.pk, admin_user.pk)
    return locked


@transaction.atomic
def unban_user(*, admin_user: User, user: User) -> User:
    """Let a banned ``user`` sign in again, with everything they had.

    Raises:
        ModerationError: If ``user`` is not banned. A self-deactivated account is
            not an admin's to restore: its owner brings it back by signing in.
    """
    locked = User.objects.select_for_update().get(pk=user.pk)
    if account_status(locked) != STATUS_BANNED:
        raise ModerationError("This account is not banned.")

    locked.is_active = True
    locked.save(update_fields=["is_active"])

    logger.info("Account unbanned: user_id=%s by=%s", locked.pk, admin_user.pk)
    return locked
