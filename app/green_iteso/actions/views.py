"""DRF views for the actions domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import mixins, status, views, viewsets
from rest_framework.request import Request
from rest_framework.response import Response

from .models import ActionCategory, ActionLog, ActionMaster
from .selectors import (
    count_user_action_logs_for_local_day,
    list_active_action_categories,
    list_active_actions,
)
from .serializers import (
    ActionCategorySerializer,
    ActionLogSerializer,
    ActionMasterSerializer,
)


class ActionCategoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve action categories under /api/v1/action-categories/."""

    serializer_class = ActionCategorySerializer

    def get_queryset(self) -> QuerySet[ActionCategory]:
        return list_active_action_categories()


class ActionMasterViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve action definitions under /api/v1/actions/."""

    serializer_class = ActionMasterSerializer

    def get_queryset(self) -> QuerySet[ActionMaster]:
        return list_active_actions()


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
            # Serialize this user's submissions so concurrent requests cannot
            # both pass the daily count before either inserts its log.
            user = type(user).objects.select_for_update().get(pk=user.pk)
            if (
                count_user_action_logs_for_local_day(user.id, action.id)
                >= action.daily_limit
            ):
                return Response(
                    {"error": "Daily limit reached for this action."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            profile = user.profile
            institutional_clan = profile.institutional_clan

            active_membership = user.clan_memberships.filter(
                is_active_private=True
            ).first()
            private_clan = active_membership.clan if active_membership else None

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
                # pylint: disable=fixme
                # TODO: Transactionalize point changes to avoid race conditions
                # available_points is the spendable balance introduced by T2-02
                # (see UserProfile.available_points); it accrues alongside
                # total_points and only total_points is drawn down separately
                # by the (future) redemption flow.
                profile.total_points += action.points
                profile.available_points += action.points
                profile.save(update_fields=["total_points", "available_points"])

                if institutional_clan:
                    institutional_clan.total_points += action.points
                    institutional_clan.save(update_fields=["total_points"])

                if private_clan:
                    private_clan.total_points += action.points
                    private_clan.save(update_fields=["total_points"])

        return Response(
            {
                "message": "Action logged successfully.",
                "status": log_status,
                "log_id": action_log.id,
            },
            status=status.HTTP_201_CREATED,
        )
