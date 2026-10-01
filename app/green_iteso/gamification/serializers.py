"""DRF serializers for the gamification domain."""

from __future__ import annotations

from rest_framework import serializers

from green_iteso.accounts.models import UserProfile

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
