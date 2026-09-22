"""DRF serializers for the clans domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from green_iteso.accounts.models import Clan, UserProfile


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.

    ``type`` is read-only: every clan created through this serializer is
    PRIVATE (see ``create_private_clan``, T2-31). Institutional clans are
    only ever produced by ``assign_institutional_clan`` (T2-30), which does
    not go through this serializer.
    """

    class Meta:
        model = Clan
        fields = [
            "id",
            "name",
            "description",
            "avatar_object_key",
            "type",
            "privacy",
            "total_points",
            "created_at",
        ]
        read_only_fields = ["id", "type", "total_points", "created_at"]


class InstitutionalOnboardingSerializer(serializers.Serializer):
    """Input payload for declaring a career during onboarding (T2-30)."""

    career = serializers.CharField(max_length=150, allow_blank=False)

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class InstitutionalAssignmentSerializer(serializers.ModelSerializer):
    """Read-only view of the caller's institutional clan assignment."""

    institutional_clan = ClanSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = ["career", "institutional_clan", "onboarding_completed_at"]
        read_only_fields = fields
