"""DRF serializers for the actions domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import ActionCategory, ActionMaster


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

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ActionLogAuditSerializer(serializers.Serializer):
    """Serializer to validate action log audit data from admins."""

    status = serializers.ChoiceField(choices=["APPROVED", "REJECTED"])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("status") == "REJECTED" and not attrs.get("rejection_reason"):
            raise serializers.ValidationError(
                {"rejection_reason": "Se requiere un motivo al rechazar la evidencia."}
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError
