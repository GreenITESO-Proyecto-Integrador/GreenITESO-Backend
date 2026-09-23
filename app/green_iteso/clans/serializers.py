"""DRF serializers for the clans domain."""

from __future__ import annotations

from rest_framework import serializers

from green_iteso.accounts.models import Clan, UserProfile


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.create_clan."""

    class Meta:
        """Field configuration for ``ClanSerializer``."""

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


class InstitutionalOnboardingSerializer(serializers.Serializer):  # pylint: disable=too-few-public-methods
    """Input payload for declaring a career during onboarding (T2-30)."""

    career = serializers.CharField(max_length=150, allow_blank=False)

    def create(self, validated_data: dict) -> None:
        raise NotImplementedError

    def update(self, instance: object, validated_data: dict) -> None:
        raise NotImplementedError


class InstitutionalAssignmentSerializer(serializers.ModelSerializer):  # pylint: disable=too-few-public-methods
    """Read-only view of the caller's institutional clan assignment."""

    institutional_clan = ClanSerializer(read_only=True)

    class Meta:
        """Field configuration for ``InstitutionalAssignmentSerializer``."""

        model = UserProfile
        fields = ["career", "institutional_clan", "onboarding_completed_at"]
        read_only_fields = fields
