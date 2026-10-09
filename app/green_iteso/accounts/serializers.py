"""DRF serializers for the accounts domain."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from rest_framework import serializers

from green_iteso.actions.models import ActionCategory

from .models import User, UserProfile, UserRoleAudit
from .selectors import EcologicalProfile

_ALLOWED_AVATAR_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_MAX_PREFERENCES_BYTES = 10_000


class UserSerializer(serializers.ModelSerializer):
    """Read-only representation of an institutional account."""

    class Meta:
        model = User
        fields = ["id", "email", "role", "date_joined"]
        read_only_fields = fields


class ChangeRoleRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Payload to modify a user's global role."""

    role = serializers.ChoiceField(
        choices=User.Role.choices,
        help_text="The new global role to assign (STUDENT, STAFF, or ADMIN).",
    )


class UserRoleAuditSerializer(serializers.ModelSerializer):
    """Audit entry representation for role change history."""

    class Meta:
        model = UserRoleAudit
        fields = [
            "id",
            "user",
            "changed_by",
            "previous_role",
            "new_role",
            "created_at",
        ]
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
    bio = serializers.CharField(source="profile.bio")
    preferences = serializers.JSONField(source="profile.preferences")
    unlocked_cosmetics = serializers.ListField(
        source="profile.unlocked_cosmetics",
        child=serializers.CharField(),
        default=list,
    )
    avatar_url = serializers.CharField(source="profile.avatar_url")
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


class ProfileMetricsQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Optional query params narrowing GET /profile/me/metrics/ (#126)."""

    category = serializers.SlugRelatedField(
        slug_field="code", queryset=ActionCategory.objects.all(), required=False
    )
    granularity = serializers.ChoiceField(
        choices=["week", "month"], required=False, default="week"
    )

    def get_fields(self) -> dict[str, serializers.Field]:
        # ``from`` is a Python keyword, so these can't be class attributes.
        fields = super().get_fields()
        fields["from"] = serializers.DateField(required=False, source="date_from")
        fields["to"] = serializers.DateField(required=False, source="date_to")
        return fields

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if date_from is not None and date_to is not None and date_from > date_to:
            raise serializers.ValidationError({"from": "from must not be after to."})
        return attrs


class CategoryActivitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Approved actions and points in one action category."""

    code = serializers.CharField()
    name = serializers.CharField()
    approved_actions = serializers.IntegerField()
    points = serializers.IntegerField()


class PeriodActivitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Approved actions and points in one week or month."""

    period_start = serializers.DateField()
    approved_actions = serializers.IntegerField()
    points = serializers.IntegerField()


class MetricsFiltersSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The filters applied, echoed back with the public query-param names."""

    granularity = serializers.CharField()
    category = serializers.CharField(allow_null=True)

    def get_fields(self) -> dict[str, serializers.Field]:
        # ``from`` is a Python keyword, so these can't be class attributes.
        fields = super().get_fields()
        fields["from"] = serializers.DateField(source="date_from", allow_null=True)
        fields["to"] = serializers.DateField(source="date_to", allow_null=True)
        return fields


class ActionMetricsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Approved actions and the points they earned."""

    approved = serializers.IntegerField()
    points_earned = serializers.IntegerField()
    by_category = CategoryActivitySerializer(many=True)


class PointsBalanceSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Current points balance of the caller."""

    total = serializers.IntegerField()
    available = serializers.IntegerField()


class AchievementMetricsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Badges earned and finished campaigns."""

    badges_earned = serializers.IntegerField()
    finished_campaigns = serializers.IntegerField()


class ProfileMetricsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Ecological-profile metrics of the caller (#126)."""

    filters = MetricsFiltersSerializer()
    impact = ImpactMetricsSerializer()
    actions = ActionMetricsSerializer()
    points = PointsBalanceSerializer()
    activity = PeriodActivitySerializer(many=True)
    achievements = AchievementMetricsSerializer()


class ImpactTrendPointSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One week of the caller's own approved-action impact totals."""

    week_start = serializers.DateField()
    co2_kg = serializers.DecimalField(max_digits=12, decimal_places=3)
    water_liters = serializers.DecimalField(max_digits=12, decimal_places=3)
    plastic_kg = serializers.DecimalField(max_digits=12, decimal_places=3)


def _validate_avatar_url(value: str) -> None:
    """Sanity-check a client-supplied avatar URL (T2-21).

    The file already lives in Cloud Storage by the time this runs -- GCS
    isn't provisioned yet (see docs/avatar-upload.md), so there is no live
    upload here to check the real content-type/size against, and fetching
    an arbitrary client-given URL server-side to inspect it would be an
    SSRF risk. This only rejects obviously wrong input (wrong scheme,
    disallowed extension); real size/type enforcement is a follow-up once
    a signed-upload flow through our own backend exists.
    """
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise serializers.ValidationError("avatar_url must use https.")
    if not parsed.path.lower().endswith(_ALLOWED_AVATAR_EXTENSIONS):
        raise serializers.ValidationError(
            "avatar_url must point to a .jpg, .jpeg, .png, or .webp file."
        )


def _validate_preferences(value: object) -> None:
    if not isinstance(value, dict):
        raise serializers.ValidationError("preferences must be a JSON object.")
    if len(json.dumps(value)) > _MAX_PREFERENCES_BYTES:
        raise serializers.ValidationError("preferences payload is too large.")


class ProfileUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Input payload for PATCH /api/v1/profile/me/ (T2-21).

    Every field is optional and independently validated; only the fields
    actually provided are changed (partial update) -- see
    ``accounts.services.update_profile``.
    """

    bio = serializers.CharField(max_length=500, allow_blank=True, required=False)
    preferences = serializers.JSONField(
        required=False, validators=[_validate_preferences]
    )
    visibility = serializers.ChoiceField(
        choices=UserProfile.Visibility.choices, required=False
    )
    avatar_url = serializers.URLField(
        max_length=500,
        allow_blank=True,
        required=False,
        validators=[_validate_avatar_url],
    )
