"""Read-only queries for the clans domain."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from green_iteso.accounts.models import Clan, ClanMembership, User


def list_active_clans(*, user: User, search: str = "") -> QuerySet[Clan]:
    """List alive clans visible to ``user``: every PUBLIC one, plus any
    PRIVATE_INVITE clan where the user has an ACCEPTED membership.

    A PENDING or REJECTED row must not grant visibility: an applicant
    shouldn't see a private clan's roster just by having a pending request.
    """
    queryset = (
        Clan.objects.filter(
            Q(privacy=Clan.Privacy.PUBLIC)
            | Q(
                memberships__user=user,
                memberships__status=ClanMembership.Status.ACCEPTED,
            )
        )
        .distinct()
        .order_by("-total_points", "name")
    )
    if search:
        queryset = queryset.filter(name__icontains=search)
    return queryset
