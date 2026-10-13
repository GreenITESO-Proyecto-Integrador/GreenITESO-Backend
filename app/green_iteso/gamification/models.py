"""Gamification models for badges, achievements, and user rewards."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Badge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField()
    points_required = models.PositiveIntegerField(
        default=0, help_text="Puntos totales requeridos para desbloquear esta medalla."
    )
    icon_name = models.CharField(
        max_length=50,
        blank=True,
        help_text="Nombre del ícono de Lucide para el frontend (ej. 'leaf', 'award').",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "gamification_badge"
        ordering = ["points_required", "name"]

    def __str__(self) -> str:
        return self.name


class UserBadge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges"
    )
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="earned_by")
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "gamification_user_badge"
        constraints = [
            models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge")
        ]
        ordering = ["-earned_at"]

    def __str__(self) -> str:
        return f"{self.user_id} earned {self.badge.name}"
