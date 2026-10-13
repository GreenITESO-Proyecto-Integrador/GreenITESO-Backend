from __future__ import annotations

from django.utils.timesince import timesince
from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(
        source="get_notification_type_display", read_only=True
    )
    time_since_created = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "user_id",
            "title",
            "message",
            "notification_type",
            "notification_type_display",
            "is_read",
            "created_at",
            "time_since_created",
        ]
        read_only_fields = [
            "id",
            "user_id",
            "title",
            "message",
            "notification_type",
            "created_at",
        ]

    def get_time_since_created(self, notification: Notification) -> str:
        return f"{timesince(notification.created_at)} ago"
