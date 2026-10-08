"""DRF serializers for the gamification domain."""

from __future__ import annotations

from rest_framework import serializers

from green_iteso.accounts.models import Clan, UserProfile

from .models import Badge

ANONYMOUS_DISPLAY_NAME = "Usuario GreenITESO"


class UserRankingSerializer(serializers.ModelSerializer):
    """One row of the user points ranking; never exposes the email."""

    id = serializers.UUIDField(source="user_id", read_only=True)
    rank = serializers.IntegerField(read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = ["id", "rank", "display_name", "total_points"]
        read_only_fields = fields

    def get_display_name(self, profile: UserProfile) -> str:
        """Return the nickname, else the full name, else a generic label."""
        user = profile.user
        return user.nickname or user.get_full_name() or ANONYMOUS_DISPLAY_NAME


class ClanRankingSerializer(serializers.ModelSerializer):
    """One row of a clan points ranking."""

    rank = serializers.IntegerField(read_only=True)
    clan_name = serializers.CharField(source="name", read_only=True)

    class Meta:
        model = Clan
        fields = ["id", "rank", "clan_name", "total_points"]
        read_only_fields = fields


class BadgeCatalogSerializer(serializers.ModelSerializer):
    """Serializer for badges including user-specific unlock status."""

    is_earned = serializers.BooleanField(read_only=True)
    earned_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Badge
        fields = [
            "id",
            "name",
            "description",
            "points_required",
            "icon_name",
            "is_earned",
            "earned_at",
        ]
        read_only_fields = fields
