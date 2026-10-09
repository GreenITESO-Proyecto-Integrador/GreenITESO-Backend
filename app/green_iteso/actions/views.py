"""DRF views for the actions and virtual exchangeables domain."""

from __future__ import annotations

import uuid

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone
from rest_framework import mixins, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response

from green_iteso.accounts.models import Clan, UserProfile
from green_iteso.accounts.selectors import get_profile_clans
from green_iteso.core.permissions import IsAdmin, IsAuthenticated
from green_iteso.feed.services import create_shared_evidence_post
from green_iteso.gamification.services import check_and_award_badges
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import notify

from .models import ActionCategory, ActionLog, ActionMaster, ExchangeableItem
from .selectors import (
    count_user_action_logs_for_local_day,
    list_active_action_categories,
    list_active_actions,
    list_active_exchangeables,
)
from .serializers import (
    ActionCategorySerializer,
    ActionLogAuditSerializer,
    ActionLogSerializer,
    ActionMasterSerializer,
    ExchangeableItemSerializer,
    RedeemExchangeableRequestSerializer,
)
from .services import (
    AlreadyUnlockedError,
    InsufficientPointsError,
    ItemInactiveError,
    PointsAlreadySpentError,
    notify_mission_progress,
    redeem_exchangeable,
    revert_mission_progress,
    revoke_awarded_points,
)


def _format_error(exc: Exception) -> str:
    """Extract a clean string representation from an exception."""
    if hasattr(exc, "messages") and exc.messages:
        return str(exc.messages[0])
    if hasattr(exc, "message") and exc.message:
        return str(exc.message)
    return str(exc)


def _build_shared_evidence_content(
    action_def: ActionMaster, points_awarded: int
) -> str:
    """Return the feed body describing a publicly shared action."""
    return f"Nueva acción registrada: {action_def.name} (+{points_awarded} puntos)."


def _resolve_evidence_image_url(evidence_object_key: str) -> str:
    """Return the evidence reference only when it is already an absolute URL.

    Evidence is stored as a private object key and the signed-URL resolver is
    part of the pending storage work (P1), so a bare key is never published to
    the feed; the post is created without an image until that lands.
    """
    if evidence_object_key.startswith(("http://", "https://")):
        return evidence_object_key
    return ""


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
    """Full CRUD for action definitions under /api/v1/actions/."""

    serializer_class = ActionMasterSerializer

    def get_queryset(self) -> QuerySet[ActionMaster]:
        return list_active_actions()

    def get_permissions(self) -> list[BasePermission]:
        """
        Assign distinct permissions based on the invoked action.
        """

        if self.action in ["create", "update", "partial_update", "destroy"]:
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

        with transaction.atomic():
            # Serialize this user's submissions so concurrent requests cannot
            # both pass the daily count before either inserts its log.
            # A weaker row lock still serializes submissions but permits user
            # FK inserts by transactions already holding a profile-row lock.
            user = type(user).objects.select_for_update(no_key=True).get(pk=user.pk)
            existing = ActionLog.objects.filter(
                user_id=user.id, idempotency_key=data["idempotency_key"]
            ).first()
            if existing:
                if existing.action_id != data[
                    "action_id"
                ] or existing.evidence_object_key != data.get(
                    "evidence_object_key", ""
                ):
                    return Response(
                        {"error": "Idempotency key was used for a different action."},
                        status=status.HTTP_409_CONFLICT,
                    )
                return Response(
                    {
                        "message": "Action already logged.",
                        "status": existing.status,
                        "log_id": existing.id,
                    },
                    status=status.HTTP_200_OK,
                )

            try:
                action_def = ActionMaster.objects.get(
                    id=data["action_id"], is_active=True
                )
            except ActionMaster.DoesNotExist:
                return Response(
                    {"error": "Action not found or inactive."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            log_status = (
                ActionLog.Status.PENDING_AUDIT
                if action_def.validation_type == ActionMaster.ValidationType.PHOTO
                else ActionLog.Status.APPROVED
            )
            if (
                count_user_action_logs_for_local_day(user.id, action_def.id)
                >= action_def.daily_limit
            ):
                return Response(
                    {"error": "Daily limit reached for this action."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            profile, institutional_clan, private_clan = get_profile_clans(user)

            action_log = ActionLog.objects.create(
                user=user,
                action=action_def,
                institutional_clan=institutional_clan,
                credited_private_clan=private_clan,
                idempotency_key=data["idempotency_key"],
                points_awarded=action_def.points,
                co2_kg_factor_snapshot=action_def.co2_kg_factor,
                water_liters_factor_snapshot=action_def.water_liters_factor,
                plastic_kg_factor_snapshot=action_def.plastic_kg_factor,
                status=log_status,
                evidence_object_key=data.get("evidence_object_key", ""),
                is_shared_publicly=data.get("is_shared_publicly", False),
            )

            if action_log.is_shared_publicly:
                create_shared_evidence_post(
                    author=user,
                    content=_build_shared_evidence_content(
                        action_def, action_log.points_awarded
                    ),
                    image_url=_resolve_evidence_image_url(
                        action_log.evidence_object_key
                    ),
                )

            if log_status == ActionLog.Status.APPROVED:
                # Use database-side increments so concurrent approved actions
                # cannot overwrite each other's balance.
                type(profile).objects.filter(pk=profile.pk).update(
                    total_points=F("total_points") + action_def.points,
                    available_points=F("available_points") + action_def.points,
                )

                if institutional_clan:
                    type(institutional_clan).all_objects.filter(
                        pk=institutional_clan.pk
                    ).update(total_points=F("total_points") + action_def.points)

                if private_clan:
                    # Credit the ActionLog snapshot even if the clan was
                    # dissolved after the active membership was read.
                    type(private_clan).all_objects.filter(pk=private_clan.pk).update(
                        total_points=F("total_points") + action_def.points
                    )

                user.profile.refresh_from_db(
                    fields=["total_points", "available_points"]
                )
                check_and_award_badges(user)
                notify_mission_progress(action_log)

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

    def patch(self, request: Request, log_id: uuid.UUID) -> Response:
        serializer = ActionLogAuditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        new_status = data["status"]
        rejection_reason = data.get("rejection_reason", "")
        # Pending logs can be approved or rejected; an approved log can only be
        # rejected, which revokes the points and mission progress it credited.
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
                notify(
                    action_log.user,
                    Notification.NotificationType.AUDIT_REJECT,
                    reason=rejection_reason,
                )
                # Must run after the REJECTED status is saved (campaigns rule).
                revert_mission_progress(action_log)
            elif new_status == "APPROVED":
                profile = action_log.user.profile
                points = action_log.points_awarded
                UserProfile.objects.filter(pk=profile.pk).update(
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

                profile.refresh_from_db(fields=["total_points", "available_points"])
                check_and_award_badges(action_log.user)
                notify_mission_progress(action_log)

                notify(
                    action_log.user,
                    Notification.NotificationType.AUDIT_APPROVED,
                    action_name=action_log.action.name,
                    points=action_log.points_awarded,
                )

        return Response({"message": "Audit processed successfully."})


class ExchangeableItemViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, and redeem virtual exchangeables under /api/v1/exchangeables/."""

    serializer_class = ExchangeableItemSerializer

    def get_queryset(self) -> QuerySet[ExchangeableItem]:
        return list_active_exchangeables()

    @action(detail=True, methods=["post"], url_path="redeem")
    def redeem(self, request: Request, pk: str | None = None) -> Response:  # pylint: disable=unused-argument
        """Redeem a specific virtual item by its ID in the URL path."""
        item = self.get_object()
        try:
            profile = redeem_exchangeable(user=request.user, item_id=item.id)
        except (
            InsufficientPointsError,
            AlreadyUnlockedError,
            ItemInactiveError,
        ) as exc:
            return Response(
                {"error": _format_error(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": "Canjeable obtenido exitosamente.",
                "unlocked_key": item.key,
                "available_points": profile.available_points,
                "unlocked_cosmetics": profile.unlocked_cosmetics,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="redeem")
    def redeem_by_body(self, request: Request) -> Response:
        """Redeem a virtual item providing item_key or item_id in JSON payload."""
        serializer = RedeemExchangeableRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            profile = redeem_exchangeable(
                user=request.user,
                item_key=data.get("item_key"),
                item_id=data.get("item_id"),
            )
            key_redeemed = data.get("item_key")
            if not key_redeemed and data.get("item_id"):
                key_redeemed = ExchangeableItem.objects.get(id=data["item_id"]).key
        except (
            InsufficientPointsError,
            AlreadyUnlockedError,
            ItemInactiveError,
        ) as exc:
            return Response(
                {"error": _format_error(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": "Canjeable obtenido exitosamente.",
                "unlocked_key": key_redeemed,
                "available_points": profile.available_points,
                "unlocked_cosmetics": profile.unlocked_cosmetics,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="my-inventory")
    def my_inventory(self, request: Request) -> Response:
        """Return the list of unlocked cosmetic keys for the authenticated user."""
        profile = request.user.profile
        profile.refresh_from_db()
        return Response(
            {
                "unlocked_cosmetics": profile.unlocked_cosmetics or [],
            }
        )
