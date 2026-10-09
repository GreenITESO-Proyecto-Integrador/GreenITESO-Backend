"""DRF serializers for the connections (friendships) domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from green_iteso.accounts.models import Friendship, User


class UserSummarySerializer(serializers.ModelSerializer):
    """Minimal representation of the other party in a friendship."""

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name"]
        read_only_fields = fields


class FriendshipSerializer(serializers.ModelSerializer):
    """Read-only representation of a friend request/friendship."""

    requester = UserSummarySerializer(read_only=True)
    addressee = UserSummarySerializer(read_only=True)

    class Meta:
        model = Friendship
        fields = [
            "id",
            "requester",
            "addressee",
            "status",
            "created_at",
            "responded_at",
        ]
        read_only_fields = fields


class SendFriendRequestSerializer(serializers.Serializer):
    """Input payload for sending a friend request (T2-50)."""

    addressee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError
