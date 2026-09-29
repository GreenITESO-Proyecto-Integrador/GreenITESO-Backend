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
    ClanDetailSerializer,
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
)
from .services import assign_institutional_clan, create_private_clan


def _validation_error_from(
    exc: ValueError, *, field: str | None = None
) -> ValidationError:
    """Convert a domain ValueError into a DRF ValidationError (shared across actions)."""
    if field:
        return ValidationError({field: str(exc)})
    return ValidationError(str(exc))


class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, and create clans under /api/v1/clans/."""

    serializer_class = ClanSerializer

    def get_serializer_class(self) -> type[ClanSerializer]:
        if self.action == "retrieve":
            return ClanDetailSerializer
        return ClanSerializer

    def get_queryset(self) -> QuerySet[Clan]:
        queryset = list_active_clans(
            user=self.request.user, search=self.request.query_params.get("search", "")
        )
        if self.action == "retrieve":
            return queryset.prefetch_related("memberships__user")
        return queryset

    def perform_create(self, serializer: ClanSerializer) -> None:
        try:
            serializer.instance = create_private_clan(
                name=serializer.validated_data["name"],
                description=serializer.validated_data.get("description", ""),
                avatar_object_key=serializer.validated_data.get(
                    "avatar_object_key", ""
                ),
                privacy=serializer.validated_data.get("privacy", Clan.Privacy.PUBLIC),
                created_by=self.request.user,
            )
        except ValueError as exc:
            raise _validation_error_from(exc) from exc


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
            raise _validation_error_from(exc, field="career") from exc
        return Response(
            InstitutionalAssignmentSerializer(profile).data,
            status=status.HTTP_200_OK,
        )
