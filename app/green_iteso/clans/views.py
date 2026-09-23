"""DRF views for the clans domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan
from green_iteso.accounts.services import ensure_profile

from .selectors import list_active_clans
from .serializers import (
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
)
from .services import assign_institutional_clan, create_clan


class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, and create clans under /api/v1/clans/."""

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


class InstitutionalClanAssignmentView(APIView):
    """Read or declare the caller's institutional clan assignment (T2-30)."""

    def get(self, request: Request) -> Response:
        """Return the caller's current onboarding/institutional clan state."""
        profile = ensure_profile(request.user)
        return Response(InstitutionalAssignmentSerializer(profile).data)

    def post(self, request: Request) -> Response:
        """Declare a career and auto-assign its institutional clan."""
        payload = InstitutionalOnboardingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            profile = assign_institutional_clan(
                user=request.user, career=payload.validated_data["career"]
            )
        except ValueError as exc:
            raise ValidationError({"career": str(exc)}) from exc
        return Response(
            InstitutionalAssignmentSerializer(profile).data,
            status=status.HTTP_200_OK,
        )
