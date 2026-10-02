"""Serializers for the feed module."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone, translation
from django.utils.timesince import timesince
from rest_framework import serializers

from green_iteso.feed.models import Post, PostType
from green_iteso.gamification.models import UserBadge

User = get_user_model()


class PostAuthorSerializer(serializers.ModelSerializer):
    """Minimal nested author details for feed cards."""

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name"]
        read_only_fields = ["id", "first_name", "last_name"]


class PostSerializer(serializers.ModelSerializer):
    """Serializer handling validation and serialization for Post instances."""

    author = PostAuthorSerializer(read_only=True)
    author_id = serializers.PrimaryKeyRelatedField(source="author", read_only=True)
    badge_user = serializers.PrimaryKeyRelatedField(
        queryset=UserBadge.objects.all(),
        required=False,
        allow_null=True,
    )
    badge_info = serializers.SerializerMethodField()
    relative_time = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id",
            "author",
            "author_id",
            "post_type",
            "content",
            "image_url",
            "badge_user",
            "badge_info",
            "relative_time",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "author",
            "author_id",
            "badge_info",
            "relative_time",
            "created_at",
            "updated_at",
        ]

    def get_relative_time(self, obj: Post) -> str:
        """Return relative time formatted in Spanish (e.g., hace 2 horas)."""
        now = timezone.now()
        diff = now - obj.created_at
        if diff.total_seconds() < 60:
            return "hace un momento"
        with translation.override("es"):
            raw_time = timesince(obj.created_at, now).split(",")[0]
        return f"hace {raw_time}"

    def get_badge_info(self, obj: Post) -> dict[str, Any] | None:
        """Return badge metadata if associated; null otherwise."""
        if not obj.badge_user_id or not obj.badge_user:
            return None

        user_badge = obj.badge_user
        badge = user_badge.badge
        return {
            "id": str(badge.id),
            "user_badge_id": str(user_badge.id),
            "name": badge.name,
            "description": badge.description,
            "icon_name": badge.icon_name,
            "earned_at": user_badge.earned_at.isoformat(),
        }

    def validate_badge_user(self, value: UserBadge | None) -> UserBadge | None:
        """Ensure the attached badge belongs to the requesting user."""
        if value is None:
            return None

        request = self.context.get("request")
        if request and request.user.is_authenticated and value.user_id != request.user.pk:
            raise serializers.ValidationError(
                "You can only attach a badge that you have earned."
            )
        return value

    def validate_content(self, value: str) -> str:
        """Ensure content is not empty or composed solely of whitespace."""
        if not value or not value.strip():
            raise serializers.ValidationError("Content cannot be empty.")
        return value.strip()

    def validate_post_type(self, value: str) -> str:
        """Ensure post_type belongs to valid choices."""
        if value not in PostType.values:
            raise serializers.ValidationError(f"Invalid post type: {value}")
        return value

    def validate_image_url(self, value: str) -> str:
        """Ensure valid URL formatting if provided."""
        if value:
            validator = serializers.URLField()
            validator.run_validation(value)
        return value
