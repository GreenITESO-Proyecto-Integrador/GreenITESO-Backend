"""DRF serializers for the clans domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from green_iteso.accounts.avatars import user_avatar_url
from green_iteso.accounts.models import (
    Clan,
    ClanInvitation,
    ClanMembership,
    User,
    UserProfile,
)


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.create_private_clan.

    ``avatar_object_key`` is read-only: it is only ever set by the
    ``/clans/{id}/avatar/`` upload, which generates the key server-side.
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
        read_only_fields = [
            "id",
            "avatar_object_key",
            "type",
            "total_points",
            "created_at",
        ]


class ClanAvatarUploadSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Multipart input for POST /clans/{id}/avatar/ (#164).

    Only checks that a file was sent; ``clans.avatars`` validates the image
    itself (signature, type, and size).
    """

    avatar = serializers.FileField()


class ClanInvitationSerializer(serializers.ModelSerializer):
    """An active invite code as listed to the leader; never includes the code (#165)."""

    class Meta:
        model = ClanInvitation
        fields = ["id", "code_hint", "expires_at", "created_at"]
        read_only_fields = fields


class ClanInvitationCreatedSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Response to creating an invite code: the only time ``code`` is returned."""

    id = serializers.UUIDField(read_only=True)
    code = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class RedeemInvitationSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Input for POST /clans/redeem-invitation/ (#165)."""

    code = serializers.CharField(max_length=20)


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
    nickname = serializers.CharField(
        max_length=50,
        allow_blank=True,
        required=False,
        help_text=(
            "Only fills an empty nickname. If the user already has one it is "
            "kept and returned unchanged in the response."
        ),
    )
    visibility = serializers.ChoiceField(
        choices=UserProfile.Visibility.choices, required=False
    )

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

    Any authenticated user can read the roster of an institutional or PUBLIC
    clan, so a PRIVATE profile shows only its nickname, never the real name,
    the same way the ranking hides it.
    """

    user_id = serializers.UUIDField(source="user.id", read_only=True)
    nickname = serializers.CharField(source="user.nickname", read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    role = serializers.ChoiceField(
        choices=ClanMembership.MembershipRole.choices, read_only=True
    )
    joined_at = serializers.DateTimeField(read_only=True)

    def get_display_name(self, membership: ClanMembership) -> str:
        """``public_display_name``, without the real name for PRIVATE profiles."""
        user = membership.user
        profile = getattr(user, "profile", None)
        if profile is not None and profile.visibility == UserProfile.Visibility.PRIVATE:
            return user.nickname.strip() or User.ANONYMOUS_DISPLAY_NAME
        return user.public_display_name

    def get_avatar_url(self, membership: ClanMembership) -> str:
        """The member's profile photo URL (#170), or "" when there is none."""
        return user_avatar_url(membership.user, self.context.get("request"))

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
    nickname = serializers.CharField(source="user.nickname", read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            "career",
            "institutional_clan",
            "onboarding_completed_at",
            "nickname",
            "visibility",
        ]
        read_only_fields = fields


class InstitutionalCareersSerializer(serializers.Serializer):
    """Careers offered in the onboarding picker (#163)."""

    careers = serializers.ListField(child=serializers.CharField(), read_only=True)

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class UserMinimalSerializer(serializers.Serializer):
    """Minimal representation of a user for join requests (excludes personal email).

    Unlike the roster, the full name is shown even for PRIVATE profiles: the
    applicant started the request and only the clan leader sees it, who needs
    to know who is asking to join.
    """

    id = serializers.UUIDField(read_only=True)
    nickname = serializers.CharField(read_only=True)
    display_name = serializers.CharField(source="public_display_name", read_only=True)

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class PendingMembershipRequestSerializer(serializers.ModelSerializer):
    """Serializer for pending clan join requests."""

    user = UserMinimalSerializer(read_only=True)

    class Meta:
        model = ClanMembership
        fields = ["id", "clan_id", "user", "role", "status", "joined_at"]
        read_only_fields = fields


class KickMemberSerializer(serializers.Serializer):
    """Input payload for expelling a clan member (T2-41)."""

    user_id = serializers.UUIDField()

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError
