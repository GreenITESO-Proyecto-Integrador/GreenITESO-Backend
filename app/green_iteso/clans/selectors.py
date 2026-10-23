"""Read-only queries for the clans domain."""

from __future__ import annotations

import uuid

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


def list_institutional_careers() -> list[str]:
    """Return the careers users can pick during onboarding (#163).

    Each alive institutional clan is named after its career (see
    ``assign_institutional_clan``), so its names are the current catalog.
    """
    return list(
        Clan.objects.filter(type=Clan.ClanType.INSTITUTIONAL)
        .order_by("name")
        .values_list("name", flat=True)
    )


def get_pending_join_requests(*, clan_id: uuid.UUID) -> QuerySet[ClanMembership]:
    """Retorna las solicitudes de ingreso en estado PENDING para un clan."""
    return (
        ClanMembership.objects.filter(
            clan_id=clan_id,
            status=ClanMembership.Status.PENDING,
            clan__deleted_at__isnull=True,
        )
        .select_related("user")
        .order_by("-joined_at")
    )
