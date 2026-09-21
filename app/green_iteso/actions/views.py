from __future__ import annotations

from django.db import transaction
from rest_framework import status, views
from rest_framework.request import Request
from rest_framework.response import Response

from .models import ActionLog, ActionMaster
from .serializers import ActionLogSerializer


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
                profile.total_points += action.points
                profile.save(update_fields=["total_points"])

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
