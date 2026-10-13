"""DRF views for the clans domain."""

from __future__ import annotations

import uuid
from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.accounts.services import ensure_profile
from green_iteso.core.permissions import IsClanLeader

from .selectors import get_pending_join_requests, list_active_clans
from .serializers import (
    ClanDetailSerializer,
    ClanMembershipSerializer,
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
    JoinRequestDecisionSerializer,
    PendingMembershipRequestSerializer,
    TransferLeadershipSerializer,
)
from .services import (
    accept_join_request,
    assign_institutional_clan,
    create_private_clan,
    dissolve_clan,
    join_clan,
    leave_clan,
    reject_join_request,
)
from .services import select_active_private_clan as select_active_private_clan_service
from .services import transfer_leadership as transfer_leadership_service


def _validation_error_from(
    exc: ValueError, *, field: str | None = None
) -> ValidationError:
    """Convert a domain ValueError into a DRF ValidationError (shared across actions)."""
    if field:
        return ValidationError({field: str(exc)})
    return ValidationError(str(exc))


ACCEPTED_MEMBERSHIPS = Prefetch(
    "memberships",
    queryset=ClanMembership.objects.filter(
        status=ClanMembership.Status.ACCEPTED
    ).select_related("user"),
)


class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create, dissolve, join, leave, and manage clans under /api/v1/clans/."""

    serializer_class = ClanSerializer

    def get_serializer_class(self) -> type[ClanSerializer]:
        """Return the serializer class to use for the current action."""
        if self.action == "retrieve":
            return ClanDetailSerializer
        return ClanSerializer

    def get_queryset(self) -> QuerySet[Clan]:
        """Return the queryset of clans visible to the current user."""
        if self.action == "join":
            return Clan.objects.all()

        search = (
            self.request.query_params.get("search", "") if self.action == "list" else ""
        )
        queryset = list_active_clans(user=self.request.user, search=search)
        if self.action == "retrieve":
            return queryset.prefetch_related(ACCEPTED_MEMBERSHIPS)
        return queryset

    def perform_create(self, serializer: ClanSerializer) -> None:
        """Create a new private clan."""
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

    @action(
        detail=True,
        methods=["get"],
        url_path="requests",
        permission_classes=[IsAuthenticated, IsClanLeader],
    )
    def requests(self, request: Request, **kwargs: object) -> Response:
        """List pending join requests for this clan as its leader (T2-40)."""
        clan = self.get_object()
        pending_qs = get_pending_join_requests(clan_id=clan.id)
        return Response(PendingMembershipRequestSerializer(pending_qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request: Request, **kwargs: object) -> Response:
        """Request to join a clan (T2-31)."""
        clan = self.get_object()
        try:
            membership = join_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        return Response(
            ClanMembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request: Request, **kwargs: object) -> Response:
        """Leave a clan (T2-33)."""
        clan = self.get_object()
        try:
            leave_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="accept-request")
    def accept_request(self, request: Request, **kwargs: object) -> Response:
        """Accept a pending join request for this clan as its leader (T2-32)."""
        clan = self.get_object()
        payload = JoinRequestDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            applicant = User.objects.get(pk=payload.validated_data["applicant_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"applicant_id": "User not found."}) from exc
        try:
            membership = accept_join_request(
                clan=clan, actor=request.user, applicant=applicant
            )
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="reject-request")
    def reject_request(self, request: Request, **kwargs: object) -> Response:
        """Reject a pending join request for this clan as its leader (T2-32)."""
        clan = self.get_object()
        payload = JoinRequestDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            applicant = User.objects.get(pk=payload.validated_data["applicant_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"applicant_id": "User not found."}) from exc
        try:
            membership = reject_join_request(
                clan=clan, actor=request.user, applicant=applicant
            )
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="transfer-leadership")
    def transfer_leadership(self, request: Request, **kwargs: object) -> Response:
        """Transfer leadership of this clan to another member."""
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
            raise _validation_error_from(exc) from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="select-active")
    def select_active(self, request: Request, **kwargs: object) -> Response:
        """Select this clan as the user's active clan."""
        clan = self.get_object()
        try:
            membership = select_active_private_clan_service(
                user=request.user, clan=clan
            )
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        return Response(ClanMembershipSerializer(membership).data)


class InstitutionalClanAssignmentView(APIView):
    """Read or declare the caller's institutional clan assignment (T2-30)."""

    def get(self, request: Request) -> Response:
        """Return the caller's current institutional clan assignment, if any."""
        profile = ensure_profile(request.user)
        return Response(InstitutionalAssignmentSerializer(profile).data)

    def post(self, request: Request) -> Response:
        """Declare the caller's career and assign them to the corresponding institutional clan."""
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