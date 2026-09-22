"""DRF views for the clans domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import mixins, viewsets

from green_iteso.accounts.models import Clan

from .selectors import list_active_clans
from .serializers import ClanSerializer
from .services import create_clan, dissolve_clan


class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create, and dissolve clans under /api/v1/clans/."""

    serializer_class = ClanSerializer

    def get_queryset(self) -> QuerySet[Clan]:
        return list_active_clans()

    def perform_create(self, serializer: ClanSerializer) -> None:
        serializer.instance = create_clan(
            name=serializer.validated_data["name"],
            clan_type=serializer.validated_data["type"],
            description=serializer.validated_data.get("description", ""),
            created_by=self.request.user,
        )

    def perform_destroy(self, instance: Clan) -> None:
        """Dissolve the clan with a soft delete instead of removing the row (BR-09)."""
        dissolve_clan(clan=instance, actor=self.request.user)
