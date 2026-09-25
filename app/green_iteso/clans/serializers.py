"""DRF serializers for the clans domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from green_iteso.accounts.models import Clan, ClanMembership, UserProfile


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.create_clan."""

    class Meta:
        model = Clan
        fields = [
            "id",
            "name",
            "description",
            "type",
            "privacy",
            "total_points",
            "created_at",
        ]
        read_only_fields = ["id", "privacy", "total_points", "created_at"]


class TransferLeadershipSerializer(serializers.Serializer):
    """Input payload for transferring a clan's leadership (T2-42)."""

    successor_id = serializers.UUIDField()

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ClanMembershipSerializer(serializers.ModelSerializer):
    """Read-only view of a clan membership, e.g. to confirm a leadership change."""

    class Meta:
        model = ClanMembership
        fields = ["id", "clan", "user", "role", "is_active_private", "joined_at"]
        read_only_fields = fields


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
