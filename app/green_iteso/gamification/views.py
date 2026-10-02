"""DRF views for the gamification domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.request import Request
from rest_framework.response import Response

from green_iteso.accounts.models import Clan, UserProfile

from .selectors import list_clan_points_ranking, list_user_points_ranking
from .serializers import ClanRankingSerializer, UserRankingSerializer

CLAN_RANKING_TYPES = {
    "INSTITUTIONAL": Clan.ClanType.INSTITUTIONAL,
    "PRIVATE_CLAN": Clan.ClanType.PRIVATE,
}


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


class ClanRankingView(generics.ListAPIView):
    """List clans by total points under /api/v1/rankings/?type=."""

    serializer_class = ClanRankingSerializer
    pagination_class = RankingPagination

    def get_queryset(self) -> QuerySet[Clan]:
        ranking_type = self.request.query_params.get("type", "")
        clan_type = CLAN_RANKING_TYPES.get(ranking_type)
        if clan_type is None:
            raise ValidationError({"type": "Must be INSTITUTIONAL or PRIVATE_CLAN."})
        return list_clan_points_ranking(clan_type)

    def list(self, request: Request, *args: object, **kwargs: object) -> Response:
        """Add the wiki ranking_type next to the paginated results."""
        response = super().list(request, *args, **kwargs)
        response.data["ranking_type"] = request.query_params["type"]
        return response
