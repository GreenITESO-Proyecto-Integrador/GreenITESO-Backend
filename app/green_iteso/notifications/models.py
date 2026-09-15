"""User notification feed: audits, badges, campaign invites and missions."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        AUDIT_REJECT = "AUDIT_REJECT", "Photo audit rejected"
        BADGE_EARNED = "BADGE_EARNED", "Badge earned"
        CAMPAIGN_INVITE = "CAMPAIGN_INVITE", "Campaign invitation"
        MISSION_COMPLETED = "MISSION_COMPLETED", "Mission completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    title = models.CharField(max_length=150)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20, choices=NotificationType.choices
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications_notification"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    notification_type__in=[
                        "AUDIT_REJECT",
                        "BADGE_EARNED",
                        "CAMPAIGN_INVITE",
                        "MISSION_COMPLETED",
                    ]
                ),
                name="notification_type_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "is_read", "-created_at"],
                name="notification_user_read_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.notification_type}"
