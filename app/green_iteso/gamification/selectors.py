"""Read queries for the gamification domain."""

from __future__ import annotations

from django.db.models import Exists, F, OuterRef, QuerySet, Subquery, Window
from django.db.models.functions import Rank

from green_iteso.accounts.models import Clan, User, UserProfile

from .models import Badge, UserBadge


def list_user_points_ranking() -> QuerySet[UserProfile]:
    """Return public profiles of active users ranked by total points.

    Ordered by ``total_points`` descending with the user id as a stable
    tiebreaker, so limit/offset pages never repeat or skip rows. Tied users
    share the same ``rank`` (1, 1, 3, ...). The query matches the partial
    ``profile_public_ranking_idx`` index and joins the user in the same
    query, loading only the columns the ranking exposes.
    """
    return (
        UserProfile.objects.filter(
            visibility=UserProfile.Visibility.PUBLIC, user__is_active=True
        )
        .select_related("user")
        .only(
            "total_points",
            "user__id",
            "user__nickname",
            "user__first_name",
            "user__last_name",
        )
        .annotate(rank=Window(Rank(), order_by=F("total_points").desc()))
        .order_by("-total_points", "user_id")
    )


def list_clan_points_ranking(clan_type: str) -> QuerySet[Clan]:
    """Return living clans of one type ranked by total points.

    ``clan_type`` is ``INSTITUTIONAL`` or ``PRIVATE``. Soft-deleted clans stay
    out through the default manager. Tied clans share ``rank``. The id
    tiebreaker keeps limit/offset pages stable. ``clan_type_points_idx`` covers
    the type filter and the points order.
    """
    return (
        Clan.objects.filter(type=clan_type)
        .only("id", "name", "total_points")
        .annotate(rank=Window(Rank(), order_by=F("total_points").desc()))
        .order_by("-total_points", "id")
    )


def list_badges_with_user_status(user: User) -> QuerySet[Badge]:
    """Return active badges annotated with the user's earned status and date."""
    user_badges = UserBadge.objects.filter(badge=OuterRef("pk"), user=user)

    return (
        Badge.objects.filter(is_active=True)
        .annotate(
            is_earned=Exists(user_badges),
            earned_at=Subquery(user_badges.values("earned_at")[:1]),
        )
        .order_by("points_required", "name")
    )
