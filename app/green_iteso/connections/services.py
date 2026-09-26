"""Write operations for the connections (friendships) domain."""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from green_iteso.accounts.models import Friendship, User


class CannotFriendSelfError(Exception):
    """Raised when a user tries to send a friend request to themselves."""


class FriendRequestAlreadyPendingError(Exception):
    """Raised when a pending request already exists for this pair."""


class AlreadyFriendsError(Exception):
    """Raised when the pair is already an accepted friendship."""


class FriendRequestNotFoundError(Exception):
    """Raised when no such friendship exists, or the caller isn't its addressee.

    Both cases share this error (rather than a 403 for the second) so a
    caller can't use the accept/reject endpoints to probe whether a
    friendship id belongs to someone else.
    """


class FriendRequestNotPendingError(Exception):
    """Raised when responding to a request that has already been resolved."""


def _ordered_pair(user_a: User, user_b: User) -> tuple[uuid.UUID, uuid.UUID]:
    return (user_a.pk, user_b.pk) if user_a.pk < user_b.pk else (user_b.pk, user_a.pk)


@transaction.atomic
def send_friend_request(*, requester: User, addressee: User) -> Friendship:
    """Create, or revive, a pending friend request between two users (T2-50).

    The pair is unique regardless of direction (AC-2): resending a request
    after a rejection reuses the existing row instead of creating a second
    one, and the row is locked first so two concurrent requests for the same
    pair can't both pass the pending/accepted checks below.
    """
    if requester.pk == addressee.pk:
        raise CannotFriendSelfError(
            "A user cannot send a friend request to themselves."
        )

    low, high = _ordered_pair(requester, addressee)
    friendship = (
        Friendship.objects.select_for_update()
        .filter(low_user=low, high_user=high)
        .first()
    )
    if friendship is None:
        return Friendship.objects.create(
            requester=requester,
            addressee=addressee,
            low_user=low,
            high_user=high,
        )
    if friendship.status == Friendship.Status.PENDING:
        raise FriendRequestAlreadyPendingError(
            "A friend request between these users is already pending."
        )
    if friendship.status == Friendship.Status.ACCEPTED:
        raise AlreadyFriendsError("These users are already friends.")

    # REJECTED: allow a fresh request, replaying whoever initiates it now.
    friendship.requester = requester
    friendship.addressee = addressee
    friendship.status = Friendship.Status.PENDING
    friendship.responded_at = None
    friendship.save(update_fields=["requester", "addressee", "status", "responded_at"])
    return friendship


@transaction.atomic
def respond_to_friend_request(
    *, friendship_id: uuid.UUID | str, responder: User, accept: bool
) -> Friendship:
    """Accept or reject a pending request; only the addressee may respond."""
    friendship = Friendship.objects.select_for_update().filter(pk=friendship_id).first()
    if friendship is None or friendship.addressee_id != responder.pk:
        raise FriendRequestNotFoundError("No pending friend request found.")
    if friendship.status != Friendship.Status.PENDING:
        raise FriendRequestNotPendingError(
            "This friend request has already been responded to."
        )

    friendship.status = (
        Friendship.Status.ACCEPTED if accept else Friendship.Status.REJECTED
    )
    friendship.responded_at = timezone.now()
    friendship.save(update_fields=["status", "responded_at"])
    return friendship
