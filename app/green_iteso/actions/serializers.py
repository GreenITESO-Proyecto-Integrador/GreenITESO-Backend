"""DRF serializers for the actions domain."""

from __future__ import annotations

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
