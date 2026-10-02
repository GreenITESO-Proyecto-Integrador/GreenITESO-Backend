"""DRF serializers for the actions domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import ActionCategory, ActionMaster, ExchangeableItem


class ActionCategorySerializer(serializers.ModelSerializer):
    """Representation of an action category."""

    class Meta:
        model = ActionCategory
        fields = ["id", "code", "name", "description", "icon"]
        read_only_fields = ["id", "code", "name", "description", "icon"]


class ActionMasterSerializer(serializers.ModelSerializer):
    """Representation of an action master definition."""

    category = ActionCategorySerializer(read_only=True)

    class Meta:
        model = ActionMaster
        fields = [
            "id",
            "code",
            "category",
            "name",
            "description",
            "points",
            "daily_limit",
            "validation_type",
            "co2_kg_factor",
            "water_liters_factor",
            "plastic_kg_factor",
            "is_active",
        ]
        read_only_fields = fields


class ActionLogSerializer(serializers.Serializer):
    """Serializer to validate incoming data for action logging."""

    action_id = serializers.UUIDField()
    idempotency_key = serializers.CharField(max_length=128)
    evidence_object_key = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    is_shared_publicly = serializers.BooleanField(required=False, default=False)

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ExchangeableItemSerializer(serializers.ModelSerializer):
    """Representation of a virtual exchangeable item in the shop."""

    class Meta:
        model = ExchangeableItem
        fields = [
            "id",
            "key",
            "name",
            "description",
            "category",
            "points_cost",
            "is_active",
            "image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RedeemExchangeableRequestSerializer(serializers.Serializer):
    """Validation serializer when redeeming a virtual item via JSON body."""

    item_id = serializers.UUIDField(required=False)
    item_key = serializers.CharField(max_length=50, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("item_id") and not attrs.get("item_key"):
            raise serializers.ValidationError(
                "Debes enviar 'item_key' o 'item_id' en el cuerpo de la petición."
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError
