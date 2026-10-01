"""DRF views for the clans domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan, User
from green_iteso.accounts.services import ensure_profile

from .selectors import list_active_clans
from .serializers import (
    ClanDetailSerializer,
    ClanMembershipSerializer,
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
    TransferLeadershipSerializer,
)
from .services import assign_institutional_clan, create_private_clan, dissolve_clan
from .services import select_active_private_clan as select_active_private_clan_service
from .services import transfer_leadership as transfer_leadership_service


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
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create, and dissolve clans under /api/v1/clans/."""

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

    def perform_destroy(self, instance: Clan) -> None:
        """Dissolve the clan with a soft delete instead of removing the row (BR-09)."""
        dissolve_clan(clan=instance, actor=self.request.user)

    @action(detail=True, methods=["post"], url_path="transfer-leadership")
    def transfer_leadership(self, request: Request, **kwargs: object) -> Response:
        """Transfer this clan's leadership to another of its members (T2-42).

        Takes ``**kwargs`` rather than a named ``pk`` because the value is
        never read directly here: ``self.get_object()`` already resolves it
        from ``self.kwargs`` (set by ``dispatch()``), applying the view's
        queryset and permissions in the process.
        """
        clan = self.get_object()
        payload = TransferLeadershipSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            successor = User.objects.get(pk=payload.validated_data["successor_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"successor_id": "User not found."}) from exc
        try:
            membership = transfer_leadership_service(
                clan=clan, actor=request.user, successor=successor
            )
        except ValueError as exc:
            raise ValidationError({"successor_id": str(exc)}) from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="select-active")
    def select_active(self, request: Request, **kwargs: object) -> Response:
        """Set this clan as the caller's active private clan (T2-35).

        Takes ``**kwargs`` rather than a named ``pk`` because the value is
        never read directly here: ``self.get_object()`` already resolves it
        from ``self.kwargs`` (set by ``dispatch()``), applying the view's
        queryset and permissions in the process.
        """
        clan = self.get_object()
        try:
            membership = select_active_private_clan_service(
                user=request.user, clan=clan
            )
        except ValueError as exc:
            raise ValidationError({"clan": str(exc)}) from exc
        return Response(ClanMembershipSerializer(membership).data)


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
