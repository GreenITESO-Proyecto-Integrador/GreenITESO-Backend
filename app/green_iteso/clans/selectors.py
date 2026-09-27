"""Read-only queries for the clans domain."""

from __future__ import annotations

from django.db.models import QuerySet

from green_iteso.accounts.models import Clan


def list_active_clans(*, search: str = "") -> QuerySet[Clan]:
    """Return non-deleted clans for the discovery listing (T2-34).

    Ordered by total points (descending) then name, so the highest-scoring
    clans surface first and ties resolve alphabetically. When ``search`` is
    given, restricts to clans whose name contains it (case-insensitive).
    """
    queryset = Clan.objects.order_by("-total_points", "name")
    if search:
        queryset = queryset.filter(name__icontains=search)
    return queryset
