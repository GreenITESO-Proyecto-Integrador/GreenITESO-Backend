"""DRF views for the actions and virtual exchangeables domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import mixins, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .models import ActionCategory, ActionLog, ActionMaster, ExchangeableItem
from .selectors import (
    list_active_action_categories,
    list_active_actions,
    list_active_exchangeables,
)
from .serializers import (
    ActionCategorySerializer,
    ActionLogSerializer,
    ActionMasterSerializer,
    ExchangeableItemSerializer,
    RedeemExchangeableRequestSerializer,
)
from .services import (
    AlreadyUnlockedError,
    InsufficientPointsError,
    ItemInactiveError,
    redeem_exchangeable,
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
            action_def = ActionMaster.objects.get(id=data["action_id"], is_active=True)
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

        with transaction.atomic():
            profile = user.profile
            institutional_clan = profile.institutional_clan

            active_membership = user.clan_memberships.filter(
                is_active_private=True
            ).first()
            private_clan = active_membership.clan if active_membership else None

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
            )

            if log_status == ActionLog.Status.APPROVED:
                profile.total_points += action_def.points
                profile.available_points += action_def.points
                profile.save(update_fields=["total_points", "available_points"])

                if institutional_clan:
                    institutional_clan.total_points += action_def.points
                    institutional_clan.save(update_fields=["total_points"])

                if private_clan:
                    private_clan.total_points += action_def.points
                    private_clan.save(update_fields=["total_points"])

        return Response(
            {
                "message": "Action logged successfully.",
                "status": log_status,
                "log_id": action_log.id,
            },
            status=status.HTTP_201_CREATED,
        )


def _format_error(exc: Exception) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return str(exc.messages[0])
    if hasattr(exc, "message") and exc.message:
        return str(exc.message)
    return str(exc)


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
    def redeem(self, request: Request, pk: str | None = None) -> Response:
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
            # Find the redeemed key
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
