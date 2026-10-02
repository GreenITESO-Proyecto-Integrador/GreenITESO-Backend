"""DRF views for the actions domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone
from rest_framework import mixins, status, views, viewsets
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response

from green_iteso.accounts.models import Clan, UserProfile
from green_iteso.accounts.selectors import get_profile_clans
from green_iteso.core.permissions import IsAdmin, IsAuthenticated
from green_iteso.notifications.models import Notification

from .models import ActionCategory, ActionLog, ActionMaster
from .selectors import list_active_action_categories, list_active_actions
from .serializers import (
    ActionCategorySerializer,
    ActionLogAuditSerializer,
    ActionLogSerializer,
    ActionMasterSerializer,
)
from .services import PointsAlreadySpentError, revoke_awarded_points


class ActionCategoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve action categories under /api/v1/action-categories/."""

    serializer_class = ActionCategorySerializer

    def get_queryset(self) -> QuerySet[ActionCategory]:
        return list_active_action_categories()


class ActionMasterViewSet(viewsets.ModelViewSet):
    """Full CRUD for action definitions. under /api/v1/actions/."""

    serializer_class = ActionMasterSerializer

    def get_queryset(self) -> QuerySet[ActionMaster]:
        return list_active_actions()

    def get_permissions(self) -> list[BasePermission]:
        """
        Assign distinct permissions based on the invoked action.
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAdmin]
        else:
            permission_classes = [IsAuthenticated]

        return [permission() for permission in permission_classes]


class ActionLogCreateView(views.APIView):
    """API view to process and store historical action logs atomically."""

    def post(self, request: Request) -> Response:
        """Handle POST requests to register a user action and update points."""
        serializer = ActionLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user

        try:
            action = ActionMaster.objects.get(id=data["action_id"], is_active=True)
        except ActionMaster.DoesNotExist:
            return Response(
                {"error": "Action not found or inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        log_status = (
            ActionLog.Status.PENDING_AUDIT
            if action.validation_type == ActionMaster.ValidationType.PHOTO
            else ActionLog.Status.APPROVED
        )

        with transaction.atomic():
            profile, institutional_clan, private_clan = get_profile_clans(user)

            action_log = ActionLog.objects.create(
                user=user,
                action=action,
                institutional_clan=institutional_clan,
                credited_private_clan=private_clan,
                idempotency_key=data["idempotency_key"],
                points_awarded=action.points,
                co2_kg_factor_snapshot=action.co2_kg_factor,
                water_liters_factor_snapshot=action.water_liters_factor,
                plastic_kg_factor_snapshot=action.plastic_kg_factor,
                status=log_status,
                evidence_object_key=data.get("evidence_object_key", ""),
            )

            if log_status == ActionLog.Status.APPROVED:
                # Use database-side increments so concurrent approved actions
                # and a shared-dev seed cannot overwrite each other's balance.
                type(profile).objects.filter(pk=profile.pk).update(
                    total_points=F("total_points") + action.points,
                    available_points=F("available_points") + action.points,
                )

                if institutional_clan:
                    type(institutional_clan).all_objects.filter(
                        pk=institutional_clan.pk
                    ).update(total_points=F("total_points") + action.points)

                if private_clan:
                    # Credit the ActionLog snapshot even if the clan was
                    # dissolved after the active membership was read.
                    type(private_clan).all_objects.filter(pk=private_clan.pk).update(
                        total_points=F("total_points") + action.points
                    )

        return Response(
            {
                "message": "Action logged successfully.",
                "status": log_status,
                "log_id": action_log.id,
            },
            status=status.HTTP_201_CREATED,
        )


class ActionLogAuditView(views.APIView):
    """API view for administrators to audit pending logs or revoke approved ones."""

    permission_classes = [IsAdmin]

    def patch(self, request: Request, log_id: str) -> Response:
        """Process an audit decision and notify the user if rejected."""
        serializer = ActionLogAuditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        new_status = data["status"]
        rejection_reason = data.get("rejection_reason", "")
        # Pending logs can be approved or rejected; an approved log can only be
        # rejected, which revokes the points it already credited.
        auditable_statuses = [ActionLog.Status.PENDING_AUDIT]
        if new_status == "REJECTED":
            auditable_statuses.append(ActionLog.Status.APPROVED)

        with transaction.atomic():
            try:
                action_log = ActionLog.objects.select_for_update().get(
                    id=log_id, status__in=auditable_statuses
                )
            except ActionLog.DoesNotExist:
                return Response(
                    {"error": "Auditable action log not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if action_log.status == ActionLog.Status.APPROVED:
                try:
                    revoke_awarded_points(action_log)
                except PointsAlreadySpentError:
                    return Response(
                        {"error": "The user already spent the points to revoke."},
                        status=status.HTTP_409_CONFLICT,
                    )

            action_log.status = new_status
            action_log.rejection_reason = rejection_reason
            action_log.reviewed_by = request.user
            action_log.reviewed_at = timezone.now()
            action_log.save(
                update_fields=[
                    "status",
                    "rejection_reason",
                    "reviewed_by",
                    "reviewed_at",
                ]
            )

            if new_status == "REJECTED":
                Notification.objects.create(
                    user=action_log.user,
                    title="Evidencia Rechazada",
                    message=f"Tu evidencia fue rechazada. Motivo: {rejection_reason}",
                    notification_type=Notification.NotificationType.AUDIT_REJECT,
                )
            elif new_status == "APPROVED":
                points = action_log.points_awarded
                UserProfile.objects.filter(user_id=action_log.user_id).update(
                    total_points=F("total_points") + points,
                    available_points=F("available_points") + points,
                )
                Clan.all_objects.filter(pk=action_log.institutional_clan_id).update(
                    total_points=F("total_points") + points
                )
                if action_log.credited_private_clan_id:
                    Clan.all_objects.filter(
                        pk=action_log.credited_private_clan_id
                    ).update(total_points=F("total_points") + points)

        return Response(
            {"message": f"Action log {new_status.lower()} successfully."},
            status=status.HTTP_200_OK,
        )
