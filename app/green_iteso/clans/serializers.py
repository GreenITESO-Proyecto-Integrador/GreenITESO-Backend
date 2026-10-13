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


class TransferLeadershipSerializer(serializers.Serializer):
    """Input payload for transferring a clan's leadership (T2-42)."""

    successor_id = serializers.UUIDField()

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class JoinRequestDecisionSerializer(serializers.Serializer):
    """Input payload for accepting or rejecting a pending join request (T2-32)."""

    applicant_id = serializers.UUIDField()

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ClanMembershipSerializer(serializers.ModelSerializer):
    """Read-only view of a clan membership, e.g. to confirm a leadership change,
    an active-clan selection, or a join/leave/accept/reject outcome."""

    class Meta:
        model = ClanMembership
        fields = [
            "id",
            "clan",
            "user",
            "role",
            "status",
            "is_active_private",
            "joined_at",
        ]
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


class ClanMemberSerializer(serializers.Serializer):
    """Read-only roster entry, shown on a clan's public profile (T2-34).

    Only ever serialized from an ACCEPTED membership: the view's prefetch
    for ``memberships`` is pre-filtered to ``status=ACCEPTED``, so a pending
    join request never appears on the roster.
    """

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
    member_count = serializers.SerializerMethodField()

    class Meta(ClanSerializer.Meta):
        fields = ClanSerializer.Meta.fields + ["members", "member_count"]

    def get_member_count(self, obj: Clan) -> int:
        """Reuse the view's ACCEPTED-only prefetch instead of a fresh COUNT(*)."""
        return len(obj.memberships.all())


class InstitutionalAssignmentSerializer(serializers.ModelSerializer):
    """Read-only view of the caller's institutional clan assignment."""

    institutional_clan = ClanSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = ["career", "institutional_clan", "onboarding_completed_at"]
        read_only_fields = fields
