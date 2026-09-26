"""Read-only queries for the connections (friendships) domain."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from green_iteso.accounts.models import Friendship, User


def list_own_friendships(user: User) -> QuerySet[Friendship]:
    """Return every friendship where ``user`` is either party, newest first."""
    return Friendship.objects.filter(Q(requester=user) | Q(addressee=user)).order_by(
        "-created_at"
    )
