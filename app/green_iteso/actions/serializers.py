"""DRF serializers for the actions catalog.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-18
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import ActionCategory, ActionMaster

# Public fields of an ActionMaster shared by both catalog shapes.
ACTION_MASTER_FIELDS = [
    "id",
    "code",
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


class ActionCategorySerializer(serializers.ModelSerializer):
    """Read-only representation of a catalog category."""

    class Meta:
        model = ActionCategory
        fields = ["id", "code", "name", "description", "icon"]
        read_only_fields = fields


class ActionMasterSerializer(serializers.ModelSerializer):
    """Read-only ActionMaster with its category embedded (flat catalog shape)."""

    category = ActionCategorySerializer(read_only=True)

    class Meta:
        model = ActionMaster
        fields = [*ACTION_MASTER_FIELDS, "category"]
        read_only_fields = fields


class ActionMasterSummarySerializer(serializers.ModelSerializer):
    """Read-only ActionMaster without category, for nesting under its category."""

    class Meta:
        model = ActionMaster
        fields = ACTION_MASTER_FIELDS
        read_only_fields = fields


class ActionCategoryWithActionsSerializer(serializers.ModelSerializer):
    """Category with its active actions (grouped catalog shape).

    Expects the queryset from `selectors.list_categories_with_active_actions`, which
    prefetches the filtered actions into the `active_actions` attribute.
    """

    actions = ActionMasterSummarySerializer(
        many=True, read_only=True, source="active_actions"
    )

    class Meta:
        model = ActionCategory
        fields = ["id", "code", "name", "description", "icon", "actions"]
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
