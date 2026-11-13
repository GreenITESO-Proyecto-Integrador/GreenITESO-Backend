"""Serializers for feedback."""

from __future__ import annotations

from rest_framework import serializers

from green_iteso.core.image_storage import validate_image

from .models import FeedbackSubmission


class FeedbackCreateSerializer(serializers.ModelSerializer):
    screenshot = serializers.FileField(required=False, write_only=True, allow_null=True)

    class Meta:
        model = FeedbackSubmission
        fields = [
            "id",
            "category",
            "rating",
            "subject",
            "message",
            "screenshot",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "status", "created_at"]

    def validate_subject(self, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise serializers.ValidationError("El asunto no puede estar vacío.")
        if len(trimmed) > 120:
            raise serializers.ValidationError(
                "El asunto no puede exceder los 120 caracteres."
            )
        return trimmed

    def validate_message(self, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 10:
            raise serializers.ValidationError(
                "El mensaje debe tener al menos 10 caracteres."
            )
        if len(trimmed) > 1000:
            raise serializers.ValidationError(
                "El mensaje no puede exceder los 1000 caracteres."
            )
        return trimmed

    def validate_rating(self, value: int | None) -> int | None:
        if value is not None and not (1 <= value <= 5):
            raise serializers.ValidationError("La calificación debe estar entre 1 y 5.")
        return value

    def validate_screenshot(self, value: object) -> object:
        if value is None:
            return None
        try:
            validate_image(value)  # type: ignore[arg-type]
        except ValueError as err:
            raise serializers.ValidationError(str(err)) from err
        return value


class FeedbackDetailSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    has_screenshot = serializers.SerializerMethodField()

    class Meta:
        model = FeedbackSubmission
        fields = [
            "id",
            "user_email",
            "category",
            "rating",
            "subject",
            "message",
            "has_screenshot",
            "status",
            "admin_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_has_screenshot(self, obj: FeedbackSubmission) -> bool:
        return bool(obj.screenshot_object_key)


class FeedbackAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackSubmission
        fields = ["status", "admin_notes"]
