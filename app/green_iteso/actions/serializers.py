from __future__ import annotations

from typing import Any

from rest_framework import serializers


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
