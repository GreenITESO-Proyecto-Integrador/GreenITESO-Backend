"""REST API views for campaigns, participants, and mission progress."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import ClanMembership
from green_iteso.core.permissions import IsAdmin
from green_iteso.core.roles import ClanRole, GlobalRole

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress
from .serializers import (
    CampaignDetailSerializer,
    CampaignListItemSerializer,
    CampaignParticipantSerializer,
    CampaignProposalSerializer,
    CampaignRejectSerializer,
    CampaignSerializer,
    CampaignUpdateSerializer,
    MissionSerializer,
    UserMissionProgressSerializer,
)
from .services import (
    approve_campaign,
    can_manage_campaign,
    propose_global_campaign,
    reject_campaign,
    sync_campaign_statuses,
)


class CampaignStatusSyncMixin:
    """Sync campaign statuses lazily after authentication and permissions."""

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)  # type: ignore[misc]
        sync_campaign_statuses()


class CampaignPagination(PageNumberPagination):
    """Paginate campaign collections with the API's fixed page size."""

    page_size = 20


def _leader_clan_ids(user: Any) -> set[Any]:
    """Return the ids of live clans the user leads, or an empty set.

    Precomputed once per request so serializing many campaigns' ``can_manage``
    does not issue a leadership query per campaign.
    """
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(
        ClanMembership.objects.filter(
            user=user, role=ClanRole.LEADER, clan__deleted_at__isnull=True
        ).values_list("clan_id", flat=True)
    )


def _visible_campaigns(user: Any) -> QuerySet[Campaign]:
    """Return campaigns the user may see.

    Global admins see every approved campaign, global and private, without
    needing clan membership. Everyone else sees global campaigns plus
    private campaigns of clans they belong to.
    """
    if getattr(user, "role", None) == GlobalRole.ADMIN:
        return Campaign.objects.filter(approval_status=Campaign.ApprovalStatus.APPROVED)
    return (
        Campaign.objects.filter(
            Q(scope=Campaign.Scope.GLOBAL)
            | Q(scope=Campaign.Scope.PRIVATE, target_clan__memberships__user=user)
        )
        .filter(approval_status=Campaign.ApprovalStatus.APPROVED)
        .distinct()
    )


def _annotate_is_participant(
    queryset: QuerySet[Campaign], user: Any
) -> QuerySet[Campaign]:
    """Annotate each campaign with whether the given user is enrolled."""
    if not getattr(user, "is_authenticated", False):
        return queryset.annotate(
            is_participant=Exists(CampaignParticipant.objects.none())
        )
    return queryset.annotate(
        is_participant=Exists(
            CampaignParticipant.objects.filter(campaign=OuterRef("pk"), user=user)
        )
    )


def _progress_by_campaign(
    user: Any, campaign_ids: list[Any]
) -> dict[str, list[dict[str, Any]]]:
    """Return the authenticated user's existing progress grouped by campaign."""
    if not getattr(user, "is_authenticated", False) or not campaign_ids:
        return {}

    progress_records = UserMissionProgress.objects.filter(
        user=user, mission__campaign_id__in=campaign_ids
    ).select_related("mission")
    serialized_progress = UserMissionProgressSerializer(
        progress_records, many=True
    ).data
    progress_by_campaign: dict[str, list[dict[str, Any]]] = {}
    for progress, serialized in zip(progress_records, serialized_progress, strict=True):
        progress_by_campaign.setdefault(str(progress.mission.campaign_id), []).append(
            serialized
        )
    return progress_by_campaign


class CampaignListCreateView(CampaignStatusSyncMixin, generics.ListCreateAPIView):
    """List campaigns or create a campaign with its nested missions."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CampaignPagination

    def get_serializer_context(self) -> dict[str, Any]:
        context = super().get_serializer_context()
        context["leader_clan_ids"] = _leader_clan_ids(self.request.user)
        return context

    @extend_schema(
        operation_id="campaigns_list",
        tags=["campaigns"],
        parameters=[
            OpenApiParameter(
                "scope",
                str,
                enum=[Campaign.Scope.GLOBAL, Campaign.Scope.PRIVATE],
                description="Filter by a single campaign scope.",
            ),
            OpenApiParameter(
                "scope__in",
                str,
                description="Comma-separated list of scopes, e.g. GLOBAL,PRIVATE.",
            ),
            OpenApiParameter(
                "status",
                str,
                enum=[
                    Campaign.Status.PROMOTION,
                    Campaign.Status.IN_PROGRESS,
                    Campaign.Status.FINISHED,
                ],
                description="Filter by campaign status.",
            ),
            OpenApiParameter(
                "is_active", bool, description="If true, only IN_PROGRESS campaigns."
            ),
            OpenApiParameter(
                "participating",
                bool,
                description="If true, only campaigns the user is enrolled in.",
            ),
        ],
        responses={200: CampaignListItemSerializer(many=True)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    @extend_schema(
        operation_id="campaigns_create",
        tags=["campaigns"],
        responses={
            201: CampaignSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR: field errors"),
            403: OpenApiResponse(
                description="Only administrators or the clan leader can create this campaign"
            ),
        },
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().post(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Campaign]:
        queryset = _annotate_is_participant(
            _visible_campaigns(self.request.user), self.request.user
        ).prefetch_related("missions__action", "participants")
        scope = self.request.query_params.get("scope")
        scopes = [
            value.strip()
            for value in self.request.query_params.get("scope__in", "").split(",")
            if value.strip()
        ]
        campaign_status = self.request.query_params.get("status")
        if scope:
            queryset = queryset.filter(scope=scope)
        if scopes:
            queryset = queryset.filter(scope__in=scopes)
        if campaign_status:
            queryset = queryset.filter(status=campaign_status)
        if self.request.query_params.get("is_active", "").lower() == "true":
            queryset = queryset.filter(status=Campaign.Status.IN_PROGRESS)
        if self.request.query_params.get("participating", "").lower() == "true":
            queryset = queryset.filter(participants__user=self.request.user).distinct()
        return queryset.order_by("-start_date")

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        response = super().list(request, *args, **kwargs)
        if isinstance(response.data, dict) and "results" in response.data:
            campaign_data = response.data["results"]
        else:
            campaign_data = response.data
        campaign_ids = [campaign["id"] for campaign in campaign_data]
        progress_by_campaign = _progress_by_campaign(request.user, campaign_ids)
        for campaign in campaign_data:
            campaign["user_mission_progress"] = progress_by_campaign.get(
                str(campaign["id"]), []
            )
        return response

    def perform_create(self, serializer: CampaignSerializer) -> None:
        serializer.save(creator=self.request.user)


class CampaignDetailView(CampaignStatusSyncMixin, generics.RetrieveAPIView):
    """Return one campaign with its missions and participants."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "campaign_id"

    def get_queryset(self) -> QuerySet[Campaign]:
        return _annotate_is_participant(
            _visible_campaigns(self.request.user), self.request.user
        ).prefetch_related("missions__action", "participants")

    @extend_schema(
        operation_id="campaigns_retrieve",
        tags=["campaigns"],
        responses={
            200: CampaignDetailSerializer,
            404: OpenApiResponse(
                description="Campaign does not exist or is not visible"
            ),
        },
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        campaign = self.get_object()
        context = {
            "request": request,
            "leader_clan_ids": _leader_clan_ids(request.user),
        }
        data = CampaignSerializer(campaign, context=context).data
        data["participants"] = CampaignParticipantSerializer(
            campaign.participants.all(), many=True, context={"request": request}
        ).data
        data["user_mission_progress"] = _progress_by_campaign(
            request.user, [campaign.pk]
        ).get(str(campaign.pk), [])
        return Response(data)

    @extend_schema(
        operation_id="campaigns_partial_update",
        tags=["campaigns"],
        request=CampaignUpdateSerializer,
        responses={
            200: CampaignSerializer,
            400: OpenApiResponse(
                description="VALIDATION_ERROR: non-editable fields, invalid dates, "
                "or campaign not in PROMOTION"
            ),
            403: OpenApiResponse(description="User cannot manage this campaign"),
            404: OpenApiResponse(
                description="Campaign does not exist or is not visible"
            ),
        },
    )
    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        visible = get_object_or_404(
            _visible_campaigns(request.user).select_related("target_clan"),
            pk=kwargs["campaign_id"],
        )
        if not can_manage_campaign(request.user, visible):
            raise PermissionDenied("You cannot edit this campaign.")
        with transaction.atomic():
            campaign = Campaign.objects.select_for_update().get(pk=visible.pk)
            if campaign.status != Campaign.Status.PROMOTION:
                raise ValidationError(
                    "Campaigns can only be edited while in promotion."
                )
            serializer = CampaignUpdateSerializer(
                campaign, data=request.data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
        context = {
            "request": request,
            "leader_clan_ids": _leader_clan_ids(request.user),
        }
        return Response(CampaignSerializer(campaign, context=context).data)


class CampaignMissionListCreateView(
    CampaignStatusSyncMixin, generics.ListCreateAPIView
):
    """List a campaign's missions or add one while it is in promotion."""

    serializer_class = MissionSerializer
    permission_classes = [IsAuthenticated]

    @cached_property
    def campaign(self) -> Campaign:
        return get_object_or_404(
            _visible_campaigns(self.request.user), pk=self.kwargs["campaign_id"]
        )

    def get_queryset(self) -> QuerySet[Mission]:
        return (
            Mission.objects.filter(campaign=self.campaign)
            .select_related("action")
            .order_by("id")
        )

    def get_serializer_context(self) -> dict[str, Any]:
        context = super().get_serializer_context()
        context["campaign"] = self.campaign
        return context

    @extend_schema(operation_id="campaigns_missions_list", tags=["campaigns"])
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    @extend_schema(
        operation_id="campaigns_missions_create",
        tags=["campaigns"],
        responses={
            201: MissionSerializer,
            400: OpenApiResponse(
                description="VALIDATION_ERROR: repeated action, or campaign not in PROMOTION"
            ),
            403: OpenApiResponse(
                description="User cannot manage missions in this campaign"
            ),
            404: OpenApiResponse(
                description="Campaign does not exist or is not visible"
            ),
        },
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if not can_manage_campaign(request.user, self.campaign):
            raise PermissionDenied("You cannot manage missions in this campaign.")
        if self.campaign.status != Campaign.Status.PROMOTION:
            raise ValidationError(
                "Missions can only be added while the campaign is in promotion."
            )
        return self.create(request, *args, **kwargs)

    def perform_create(self, serializer: MissionSerializer) -> None:
        try:
            with transaction.atomic():
                serializer.save(campaign=self.campaign)
        except IntegrityError as error:
            raise ValidationError(
                {"action_id": "This action already has a mission in this campaign."}
            ) from error


class CampaignParticipantListView(CampaignStatusSyncMixin, generics.ListAPIView):
    """List the participants enrolled in a campaign."""

    serializer_class = CampaignParticipantSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="campaigns_participants_list",
        tags=["campaigns"],
        responses={
            200: CampaignParticipantSerializer(many=True),
            404: OpenApiResponse(
                description="Campaign does not exist or is not visible"
            ),
        },
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[CampaignParticipant]:
        campaign = get_object_or_404(
            _visible_campaigns(self.request.user), pk=self.kwargs["campaign_id"]
        )
        return CampaignParticipant.objects.filter(campaign=campaign).select_related(
            "user"
        )


class CampaignJoinView(CampaignStatusSyncMixin, APIView):
    """Enroll the authenticated user in an active campaign."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="campaigns_join",
        tags=["campaigns"],
        request=None,
        responses={
            201: CampaignParticipantSerializer,
            400: OpenApiResponse(
                description="{'detail': ...}: campaign not active or user already enrolled"
            ),
            403: OpenApiResponse(
                description="{'detail': ...}: user is not a member of this campaign's clan"
            ),
            404: OpenApiResponse(
                description="Campaign does not exist or is not approved"
            ),
        },
    )
    def post(self, request: Request, campaign_id: Any) -> Response:
        campaign = get_object_or_404(
            Campaign.objects.filter(approval_status=Campaign.ApprovalStatus.APPROVED),
            pk=campaign_id,
        )
        if campaign.status != Campaign.Status.PROMOTION:
            return Response(
                {"detail": "Campaign is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            campaign.scope == Campaign.Scope.PRIVATE
            and not ClanMembership.objects.filter(
                user=request.user, clan=campaign.target_clan
            ).exists()
        ):
            return Response(
                {"detail": "User is not a member of this campaign's clan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if CampaignParticipant.objects.filter(
            campaign=campaign, user=request.user
        ).exists():
            return Response(
                {"detail": "User is already enrolled in this campaign."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                participant = CampaignParticipant.objects.create(
                    campaign=campaign, user=request.user
                )
        except IntegrityError:
            return Response(
                {"detail": "User is already enrolled in this campaign."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            CampaignParticipantSerializer(participant).data,
            status=status.HTTP_201_CREATED,
        )


class MissionProgressView(CampaignStatusSyncMixin, APIView):
    """Read-only view of the authenticated user's progress for a mission.

    Progress is derived from ActionLog contributions by
    ``campaigns.services.apply_action_log_to_missions`` /
    ``revert_action_log_from_missions``; this endpoint cannot be written to.
    """

    permission_classes = [IsAuthenticated]

    def get_or_create_progress(
        self, request: Request, mission: Mission
    ) -> UserMissionProgress:
        """Get progress or recover the row created by a concurrent request."""
        try:
            progress, _ = UserMissionProgress.objects.get_or_create(
                user=request.user, mission=mission
            )
        except IntegrityError:
            progress = UserMissionProgress.objects.get(
                user=request.user, mission=mission
            )
        return progress

    def _require_participation(self, request: Request, mission: Mission) -> None:
        if not CampaignParticipant.objects.filter(
            campaign_id=mission.campaign_id, user=request.user
        ).exists():
            raise PermissionDenied(
                "User must join the campaign before tracking mission progress."
            )

    def get_progress(self, request: Request, mission_id: Any) -> UserMissionProgress:
        mission = get_object_or_404(Mission, pk=mission_id)
        self._require_participation(request, mission)
        return self.get_or_create_progress(request, mission)

    @extend_schema(
        operation_id="campaigns_mission_progress_retrieve",
        tags=["campaigns"],
        responses={
            200: UserMissionProgressSerializer,
            403: OpenApiResponse(
                description="User must join the campaign before tracking mission progress"
            ),
            404: OpenApiResponse(description="Mission does not exist"),
        },
    )
    def get(self, request: Request, mission_id: Any) -> Response:
        progress = self.get_progress(request, mission_id)
        return Response(UserMissionProgressSerializer(progress).data)


class CampaignProposalListCreateView(
    CampaignStatusSyncMixin, generics.ListCreateAPIView
):
    """List global campaign proposals or submit a new one for admin review."""

    serializer_class = CampaignProposalSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CampaignPagination

    @extend_schema(
        operation_id="campaigns_proposals_list",
        tags=["campaigns"],
        parameters=[
            OpenApiParameter(
                "approval_status",
                str,
                enum=[
                    Campaign.ApprovalStatus.PENDING,
                    Campaign.ApprovalStatus.APPROVED,
                    Campaign.ApprovalStatus.REJECTED,
                ],
                description="Filter proposals by approval status.",
            ),
        ],
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Campaign]:
        user = self.request.user
        queryset = Campaign.objects.filter(scope=Campaign.Scope.GLOBAL)
        if user.role == GlobalRole.ADMIN:
            queryset = queryset.exclude(creator__role=GlobalRole.ADMIN)
        else:
            queryset = queryset.filter(creator=user)
        approval_status = self.request.query_params.get("approval_status")
        if approval_status:
            queryset = queryset.filter(approval_status=approval_status)
        return queryset.order_by("-created_at")

    @extend_schema(
        operation_id="campaigns_proposals_create",
        tags=["campaigns"],
        responses={
            201: CampaignProposalSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR: field errors"),
            403: OpenApiResponse(
                description="Administrators create global campaigns directly"
            ),
        },
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if request.user.role == GlobalRole.ADMIN:
            raise PermissionDenied("Administrators create global campaigns directly.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        campaign = propose_global_campaign(request.user, serializer.validated_data)
        output_serializer = self.get_serializer(campaign)
        headers = self.get_success_headers(output_serializer.data)
        return Response(
            output_serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )


class CampaignProposalApproveView(CampaignStatusSyncMixin, APIView):
    """Approve a pending global campaign proposal."""

    permission_classes = [IsAdmin]

    @extend_schema(
        operation_id="campaigns_proposals_approve",
        tags=["campaigns"],
        request=None,
        responses={
            200: CampaignProposalSerializer,
            400: OpenApiResponse(description="Proposal is not pending review"),
            403: OpenApiResponse(
                description="Only administrators can approve proposals"
            ),
            404: OpenApiResponse(description="Proposal does not exist"),
        },
    )
    def post(self, request: Request, campaign_id: Any) -> Response:
        try:
            campaign = approve_campaign(request.user, campaign_id)
        except Campaign.DoesNotExist as error:
            raise Http404 from error
        return Response(CampaignProposalSerializer(campaign).data)


class CampaignProposalRejectView(CampaignStatusSyncMixin, APIView):
    """Reject a pending global campaign proposal with a required reason."""

    permission_classes = [IsAdmin]

    @extend_schema(
        operation_id="campaigns_proposals_reject",
        tags=["campaigns"],
        request=CampaignRejectSerializer,
        responses={
            200: CampaignProposalSerializer,
            400: OpenApiResponse(
                description="VALIDATION_ERROR: proposal not pending, or missing rejection_reason"
            ),
            403: OpenApiResponse(
                description="Only administrators can reject proposals"
            ),
            404: OpenApiResponse(description="Proposal does not exist"),
        },
    )
    def post(self, request: Request, campaign_id: Any) -> Response:
        serializer = CampaignRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            campaign = reject_campaign(
                request.user,
                campaign_id,
                serializer.validated_data["rejection_reason"],
            )
        except Campaign.DoesNotExist as error:
            raise Http404 from error
        return Response(CampaignProposalSerializer(campaign).data)
