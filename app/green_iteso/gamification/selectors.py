"""Read queries for the gamification domain."""

from __future__ import annotations

from django.db.models import F, QuerySet, Window
from django.db.models.functions import Rank

from green_iteso.accounts.models import UserProfile


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
