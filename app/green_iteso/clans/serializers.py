"""DRF serializers for the clans domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from green_iteso.accounts.models import Clan, ClanMembership, UserProfile


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.create_private_clan."""

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


class ClanMemberSerializer(serializers.Serializer):
    """Read-only roster entry, shown on a clan's public profile (T2-34)."""

    user_id = serializers.UUIDField(source="user.id", read_only=True)
    nickname = serializers.CharField(source="user.nickname", read_only=True)
    role = serializers.ChoiceField(
        choices=ClanMembership.MembershipRole.choices, read_only=True
    )
    joined_at = serializers.DateTimeField(read_only=True)

    def create(self, validated_data: dict) -> ClanMembership:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: ClanMembership, validated_data: dict) -> ClanMembership:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ClanDetailSerializer(ClanSerializer):
    """Clan profile: base representation plus its member roster (T2-34)."""

    members = ClanMemberSerializer(source="memberships", many=True, read_only=True)
    member_count = serializers.IntegerField(source="memberships.count", read_only=True)

    class Meta(ClanSerializer.Meta):
        fields = ClanSerializer.Meta.fields + ["members", "member_count"]


class InstitutionalAssignmentSerializer(serializers.ModelSerializer):
    """Read-only view of the caller's institutional clan assignment."""

    institutional_clan = ClanSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = ["career", "institutional_clan", "onboarding_completed_at"]
        read_only_fields = fields
