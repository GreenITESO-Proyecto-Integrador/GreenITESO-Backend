"""Feedback submissions model."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q


class FeedbackSubmission(models.Model):
    """User feedback, bug report, or eco suggestion."""

    class Category(models.TextChoices):
        BUG = "BUG", "Error técnico"
        IDEA = "IDEA", "Propuesta de mejora"
        ECO_ACTION = "ECO_ACTION", "Nueva acción o campaña"
        ACCESSIBILITY = "ACCESSIBILITY", "Accesibilidad o vista"
        OTHER = "OTHER", "Otro comentario"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Recibido"
        IN_REVIEW = "IN_REVIEW", "En revisión"
        RESOLVED = "RESOLVED", "Resuelto"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_submissions",
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    subject = models.CharField(max_length=120)
    message = models.TextField(max_length=1000)
    screenshot_object_key = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    admin_notes = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "feedback_submission"
        ordering = ["-created_at"]
        constraints = [
            CheckConstraint(
                condition=Q(rating__isnull=True)
                | (Q(rating__gte=1) & Q(rating__lte=5)),
                name="feedback_rating_range",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.subject} ({self.user})"
