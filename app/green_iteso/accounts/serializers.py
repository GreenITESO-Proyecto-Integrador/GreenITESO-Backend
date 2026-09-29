"""DRF serializers for the accounts domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import User
from .selectors import EcologicalProfile


class UserSerializer(serializers.ModelSerializer):
    """Read-only representation of an institutional account."""

    class Meta:
        model = User
        fields = ["id", "email", "role", "date_joined"]
        read_only_fields = fields


class LoginRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Tokens the SPA obtained from Microsoft (MSAL) for the signed-in user."""

    id_token = serializers.CharField(trim_whitespace=True, max_length=8192)
    access_token = serializers.CharField(
        trim_whitespace=True, max_length=8192, allow_blank=True, default=""
    )


class LoginUserSerializer(serializers.ModelSerializer):
    """The account summary returned next to the JWT pair."""

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role"]
        read_only_fields = fields


class LoginResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """JWT pair plus the account; ``created`` is true on the first login."""

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = LoginUserSerializer()
    created = serializers.BooleanField()


class ClanSummarySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Minimal clan reference embedded in the ecological profile."""

    id = serializers.UUIDField()
    name = serializers.CharField()


class ImpactMetricsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Sum of the environmental impact of the caller's own approved actions."""

    co2_kg = serializers.DecimalField(max_digits=12, decimal_places=3)
    water_liters = serializers.DecimalField(max_digits=12, decimal_places=3)
    plastic_kg = serializers.DecimalField(max_digits=12, decimal_places=3)


class FinishedCampaignSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Minimal reference to a campaign the caller participated in and that ended."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    end_date = serializers.DateTimeField()


class EcologicalProfileSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Aggregated self-profile: own data plus E1/E3 data (T2-20).

    ``level``/``badges`` are placeholders (``null``/``[]``) until E1 ships a
    leveling system and ``Badge``/``UserBadge``; see
    ``accounts.selectors.get_ecological_profile``.
    """

    user_id = serializers.UUIDField(source="user.id")
    email = serializers.EmailField(source="user.email")
    first_name = serializers.CharField(source="user.first_name")
    last_name = serializers.CharField(source="user.last_name")
    role = serializers.CharField(source="user.role")
    visibility = serializers.CharField(source="profile.visibility")
    total_points = serializers.IntegerField(source="profile.total_points")
    available_points = serializers.IntegerField(source="profile.available_points")
    current_streak = serializers.IntegerField(source="profile.current_streak")
    level = serializers.IntegerField(allow_null=True)
    badges = serializers.ListField(child=serializers.DictField(), default=list)
    impact_metrics = ImpactMetricsSerializer()
    finished_campaigns = FinishedCampaignSerializer(many=True)
    institutional_clan = serializers.SerializerMethodField()
    active_private_clan = serializers.SerializerMethodField()

    def get_institutional_clan(self, obj: EcologicalProfile) -> dict[str, Any] | None:
        clan = obj.profile.institutional_clan
        return ClanSummarySerializer(clan).data if clan is not None else None

    def get_active_private_clan(self, obj: EcologicalProfile) -> dict[str, Any] | None:
        clan = obj.active_private_clan
        return ClanSummarySerializer(clan).data if clan is not None else None
