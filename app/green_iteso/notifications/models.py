"""User notification feed: audits, badges, campaign invites and missions."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class NotificationType(models.TextChoices):
    AUDIT_APPROVED = "AUDIT_APPROVED", "Photo audit approved"
    AUDIT_REJECT = "AUDIT_REJECT", "Photo audit rejected"
    BADGE_EARNED = "BADGE_EARNED", "Badge earned"
    CAMPAIGN_INVITE = "CAMPAIGN_INVITE", "Campaign invitation"
    MISSION_COMPLETED = "MISSION_COMPLETED", "Mission completed"
    SOCIAL_FOLLOW = "SOCIAL_FOLLOW", "New follower"
    SYSTEM = "SYSTEM", "System announcement"


class NotificationQuerySet(models.QuerySet):
    """Queryset helpers shared between the alive-only and unrestricted managers."""

    def alive(self) -> NotificationQuerySet:
        """Return notifications that have not been soft-deleted."""
        return self.filter(deleted_at__isnull=True)


class NotificationManager(models.Manager.from_queryset(NotificationQuerySet)):
    """Default manager: excludes soft-deleted notifications from every query."""

    def get_queryset(self) -> NotificationQuerySet:
        return super().get_queryset().alive()


class Notification(models.Model):
    NotificationType = NotificationType

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
    deleted_at = models.DateTimeField(null=True, blank=True)

    all_objects = models.Manager()
    objects = NotificationManager()

    class Meta:
        db_table = "notifications_notification"
        default_manager_name = "objects"
        base_manager_name = "all_objects"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(notification_type__in=NotificationType.values),
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
