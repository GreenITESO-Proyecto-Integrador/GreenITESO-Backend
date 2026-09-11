"""Provisional action catalog and frozen audit schema for T9a."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ActionCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, blank=True)

    def __str__(self) -> str:
        return self.code


class ActionMaster(models.Model):
    class ValidationMode(models.TextChoices):
        DECLARATIVE_BUTTON = "DECLARATIVE_BUTTON", "Declarative button"
        PHOTO = "PHOTO", "Photo"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    category = models.ForeignKey(ActionCategory, on_delete=models.PROTECT, related_name="actions")
    name = models.CharField(max_length=150)
    description = models.TextField()
    points = models.PositiveIntegerField()
    daily_limit = models.PositiveIntegerField(default=1)
    validation_mode = models.CharField(max_length=20, choices=ValidationMode.choices)
    co2_kg_factor = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    water_liters_factor = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    plastic_kg_factor = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(points__gt=0), name="action_points_positive"),
            models.CheckConstraint(condition=Q(daily_limit__gt=0), name="action_daily_limit_positive"),
            models.CheckConstraint(condition=Q(co2_kg_factor__gte=0), name="action_co2_factor_nonnegative"),
            models.CheckConstraint(condition=Q(water_liters_factor__gte=0), name="action_water_factor_nonnegative"),
            models.CheckConstraint(condition=Q(plastic_kg_factor__gte=0), name="action_plastic_factor_nonnegative"),
        ]
        indexes = [models.Index(fields=["is_active", "code"], name="action_active_code_idx")]

    def __str__(self) -> str:
        return self.code


class ActionLog(models.Model):
    class Status(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        PENDING_AUDIT = "PENDING_AUDIT", "Pending audit"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="action_logs")
    action = models.ForeignKey(ActionMaster, on_delete=models.PROTECT, related_name="logs")
    institutional_clan = models.ForeignKey(
        "accounts.Clan",
        on_delete=models.PROTECT,
        related_name="institutional_action_logs",
    )
    credited_private_clan = models.ForeignKey(
        "accounts.Clan",
        on_delete=models.PROTECT,
        related_name="private_action_logs",
        null=True,
        blank=True,
    )
    # Added in the follow-up migration after campaigns.Mission exists.
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="action_logs",
        null=True,
        blank=True,
    )
    idempotency_key = models.CharField(max_length=128)
    points_awarded = models.PositiveIntegerField()
    co2_kg_factor_snapshot = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    water_liters_factor_snapshot = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    plastic_kg_factor_snapshot = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)
    evidence_object_key = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_action_logs",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="action_log_user_idempotency_unique"),
            models.CheckConstraint(condition=~Q(idempotency_key=""), name="action_log_idempotency_nonblank"),
            models.CheckConstraint(
                condition=Q(co2_kg_factor_snapshot__gte=0),
                name="action_log_co2_snapshot_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(water_liters_factor_snapshot__gte=0),
                name="action_log_water_snapshot_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(plastic_kg_factor_snapshot__gte=0),
                name="action_log_plastic_snapshot_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "action", "created_at"], name="action_log_daily_idx"),
            models.Index(fields=["status", "created_at"], name="action_log_audit_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class ActionLogMissionContribution(models.Model):
    """One row records one mission increment caused by one action log."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_log = models.ForeignKey(ActionLog, on_delete=models.PROTECT, related_name="mission_contributions")
    mission = models.ForeignKey("campaigns.Mission", on_delete=models.PROTECT, related_name="action_contributions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["action_log", "mission"], name="action_log_mission_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.action_log_id} -> {self.mission_id}"
