"""DRF views for the gamification domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.pagination import LimitOffsetPagination

from green_iteso.accounts.models import UserProfile

from .selectors import list_user_points_ranking
from .serializers import UserRankingSerializer


class RankingPagination(LimitOffsetPagination):
    """Limit/offset paging for rankings, capped to keep pages cheap."""

    default_limit = 50
    max_limit = 100


class UserRankingView(generics.ListAPIView):
    """List users by total points under /api/v1/rankings/users/."""

    serializer_class = UserRankingSerializer
    pagination_class = RankingPagination

    def get_queryset(self) -> QuerySet[UserProfile]:
        return list_user_points_ranking()
