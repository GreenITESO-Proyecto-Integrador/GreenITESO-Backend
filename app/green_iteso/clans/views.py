"""DRF views for the clans domain."""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.accounts.services import ensure_profile

from .selectors import list_active_clans
from .serializers import (
    ClanDetailSerializer,
    ClanMembershipSerializer,
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
    JoinRequestDecisionSerializer,
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
"""Roster prefetch used by ``retrieve``: a PENDING/REJECTED row never counts
as a member, so both ``members`` and ``member_count`` on the detail
serializer stay correct without an extra query."""


class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create, dissolve, join, leave, and manage clans under
    /api/v1/clans/."""

    serializer_class = ClanSerializer

    def get_serializer_class(self) -> type[ClanSerializer]:
        if self.action == "retrieve":
            return ClanDetailSerializer
        return ClanSerializer

    def get_queryset(self) -> QuerySet[Clan]:
        # "join" is exempt from the usual visibility filter: a PRIVATE_INVITE
        # clan's applicant is expected to already know the clan (shared out
        # of band, e.g. a link from the leader), so the clan not yet showing
        # up in list_active_clans must not 404 the join attempt.
        if self.action == "join":
            return Clan.objects.all()

        # `search` only applies to `list`: `get_queryset()` also backs
        # `get_object()` for retrieve and every detail action (destroy,
        # transfer-leadership, select-active, leave, accept/reject-request),
        # where filtering by an unrelated query param would 404 a clan that
        # otherwise exists.
        search = (
            self.request.query_params.get("search", "") if self.action == "list" else ""
        )
        queryset = list_active_clans(user=self.request.user, search=search)
        if self.action == "retrieve":
            return queryset.prefetch_related(ACCEPTED_MEMBERSHIPS)
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

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request: Request, **kwargs: object) -> Response:
        """Join a PUBLIC clan directly, or file a pending request for a
        PRIVATE_INVITE one (T2-32).
        """
        clan = self.get_object()
        try:
            membership = join_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise ValidationError({"clan": str(exc)}) from exc
        return Response(
            ClanMembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request: Request, **kwargs: object) -> Response:
        """Leave a private clan the caller currently belongs to (T2-33)."""
        clan = self.get_object()
        try:
            leave_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise ValidationError({"clan": str(exc)}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="accept-request")
    def accept_request(self, request: Request, **kwargs: object) -> Response:
        """Accept a pending join request as the clan's leader (T2-32)."""
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
            raise ValidationError({"applicant_id": str(exc)}) from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="reject-request")
    def reject_request(self, request: Request, **kwargs: object) -> Response:
        """Reject a pending join request as the clan's leader (T2-32)."""
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
            raise ValidationError({"applicant_id": str(exc)}) from exc
        return Response(ClanMembershipSerializer(membership).data)

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
