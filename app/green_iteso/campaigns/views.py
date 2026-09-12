"""REST API views for campaigns, participants, and mission progress."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress
from .serializers import (
    CampaignParticipantSerializer,
    CampaignSerializer,
    UserMissionProgressSerializer,
)


class CampaignPagination(PageNumberPagination):
    """Paginate campaign collections with the API's fixed page size."""

    page_size = 20


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


class CampaignListCreateView(generics.ListCreateAPIView):
    """List campaigns or create a campaign with its nested missions."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CampaignPagination

    def get_queryset(self) -> QuerySet[Campaign]:
        queryset = Campaign.objects.prefetch_related("missions", "participants")
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
            current_time = timezone.now()
            queryset = queryset.filter(
                start_date__lte=current_time, end_date__gte=current_time
            )
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
        # TODO: enforce ADMIN/LEADER role rules when campaign authorization is defined.
        serializer.save(creator=self.request.user)


class CampaignDetailView(generics.RetrieveAPIView):
    """Return one campaign with its missions and participants."""

    queryset = Campaign.objects.prefetch_related("missions", "participants")
    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "campaign_id"

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        campaign = self.get_object()
        data = CampaignSerializer(campaign, context={"request": request}).data
        data["participants"] = CampaignParticipantSerializer(
            campaign.participants.all(), many=True, context={"request": request}
        ).data
        data["user_mission_progress"] = _progress_by_campaign(
            request.user, [campaign.pk]
        ).get(str(campaign.pk), [])
        return Response(data)


class CampaignParticipantListView(generics.ListAPIView):
    """List the participants enrolled in a campaign."""

    serializer_class = CampaignParticipantSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self) -> QuerySet[CampaignParticipant]:
        return CampaignParticipant.objects.filter(
            campaign_id=self.kwargs["campaign_id"]
        ).select_related("user")


class CampaignJoinView(APIView):
    """Enroll the authenticated user in an active campaign."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, campaign_id: Any) -> Response:
        campaign = get_object_or_404(Campaign, pk=campaign_id)
        active_statuses = {Campaign.Status.PROMOTION, Campaign.Status.IN_PROGRESS}
        if campaign.status not in active_statuses:
            return Response(
                {"detail": "Campaign is not active."},
                status=status.HTTP_400_BAD_REQUEST,
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


class MissionProgressView(APIView):
    """Read or update the authenticated user's progress for a mission."""

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

    def get_progress(self, request: Request, mission_id: Any) -> UserMissionProgress:
        mission = get_object_or_404(Mission, pk=mission_id)
        return self.get_or_create_progress(request, mission)

    def get(self, request: Request, mission_id: Any) -> Response:
        progress = self.get_progress(request, mission_id)
        return Response(UserMissionProgressSerializer(progress).data)

    def patch(self, request: Request, mission_id: Any) -> Response:
        mission = get_object_or_404(Mission, pk=mission_id)
        with transaction.atomic():
            progress = self.get_or_create_progress(request, mission)
            progress = UserMissionProgress.objects.select_for_update().get(
                pk=progress.pk
            )
            payload = request.data.copy()
            increment = payload.pop("increment", None)
            if increment is not None:
                if "current_count" in payload:
                    return Response(
                        {"detail": "Send either increment or current_count, not both."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    payload["current_count"] = progress.current_count + int(increment)
                except (TypeError, ValueError):
                    return Response(
                        {"increment": "Increment must be an integer."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            serializer = UserMissionProgressSerializer(
                progress, data=payload, partial=True
            )
            serializer.is_valid(raise_exception=True)
            if (
                serializer.validated_data.get("current_count", progress.current_count)
                > mission.target_count
            ):
                return Response(
                    {"current_count": "Progress cannot exceed the mission target."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            updated_progress = serializer.save(
                is_completed=serializer.validated_data.get(
                    "current_count", progress.current_count
                )
                >= mission.target_count
            )
        return Response(UserMissionProgressSerializer(updated_progress).data)
