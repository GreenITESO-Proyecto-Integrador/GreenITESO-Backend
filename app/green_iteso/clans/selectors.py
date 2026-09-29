"""Read-only queries for the clans domain."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from green_iteso.accounts.models import Clan, User


def list_active_clans(*, user: User, search: str = "") -> QuerySet[Clan]:
    """Return non-deleted clans visible to ``user`` for discovery (T2-34).

    A ``PRIVATE_INVITE`` clan is only visible to its own members; public
    clans (institutional clans included, since they default to
    ``Privacy.PUBLIC``) are visible to everyone. This is the single
    visibility gate: the detail view builds on this same queryset, so a
    non-member can't reach a private clan's roster either (``get_object()``
    404s once the row is filtered out here).
    """
    queryset = (
        Clan.objects.filter(Q(privacy=Clan.Privacy.PUBLIC) | Q(memberships__user=user))
        .distinct()
        .order_by("-total_points", "name")
    )
    if search:
        queryset = queryset.filter(name__icontains=search)
    return queryset
