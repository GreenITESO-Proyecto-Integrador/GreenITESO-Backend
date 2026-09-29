"""DRF serializers for the accounts domain."""

from __future__ import annotations

from rest_framework import serializers

from .models import User, UserRoleAudit


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
