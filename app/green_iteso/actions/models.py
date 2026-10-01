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

    class Meta:
        db_table = "actions_action_category"

    def __str__(self) -> str:
        return self.code


class ActionMaster(models.Model):
    class ValidationType(models.TextChoices):
        NONE = "NONE", "None"
        PHOTO = "PHOTO", "Photo"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    category = models.ForeignKey(
        ActionCategory, on_delete=models.PROTECT, related_name="actions"
    )
    name = models.CharField(max_length=150)
    description = models.TextField()
    points = models.PositiveIntegerField()
    daily_limit = models.PositiveIntegerField(default=1)
    validation_type = models.CharField(max_length=10, choices=ValidationType.choices)
    co2_kg_factor = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    water_liters_factor = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    plastic_kg_factor = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "actions_action_master"
        constraints = [
            models.CheckConstraint(
                condition=Q(points__gt=0), name="action_points_positive"
            ),
            models.CheckConstraint(
                condition=Q(daily_limit__gt=0), name="action_daily_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(validation_type__in=["NONE", "PHOTO"]),
                name="action_validation_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(co2_kg_factor__gte=0), name="action_co2_factor_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(water_liters_factor__gte=0),
                name="action_water_factor_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(plastic_kg_factor__gte=0),
                name="action_plastic_factor_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "code"], name="action_active_code_idx")
        ]

    def __str__(self) -> str:
        return self.code


class ActionLog(models.Model):
    class Status(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        PENDING_AUDIT = "PENDING_AUDIT", "Pending audit"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="action_logs"
    )
    action = models.ForeignKey(
        ActionMaster, on_delete=models.PROTECT, related_name="logs"
    )
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
    co2_kg_factor_snapshot = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    water_liters_factor_snapshot = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    plastic_kg_factor_snapshot = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.APPROVED
    )
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
        db_table = "actions_action_log"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name="action_log_user_idempotency_unique",
            ),
            models.CheckConstraint(
                condition=~Q(idempotency_key=""), name="action_log_idempotency_nonblank"
            ),
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
            models.CheckConstraint(
                condition=Q(status__in=["APPROVED", "PENDING_AUDIT", "REJECTED"]),
                name="action_log_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "action", "created_at"], name="action_log_daily_idx"
            ),
            models.Index(fields=["status", "created_at"], name="action_log_audit_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class ActionLogMissionContribution(models.Model):
    """One row records one mission increment caused by one action log."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_log = models.ForeignKey(
        ActionLog, on_delete=models.PROTECT, related_name="mission_contributions"
    )
    mission = models.ForeignKey(
        "campaigns.Mission",
        on_delete=models.PROTECT,
        related_name="action_contributions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "actions_action_log_mission_contribution"
        constraints = [
            models.UniqueConstraint(
                fields=["action_log", "mission"], name="action_log_mission_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action_log_id} -> {self.mission_id}"


class Reward(models.Model):
    """Catalog item that can be redeemed using spendable available_points."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    points_cost = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    image_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "actions_reward"
        constraints = [
            models.CheckConstraint(
                condition=Q(points_cost__gt=0), name="reward_points_cost_positive"
            ),
            models.CheckConstraint(
                condition=Q(stock__gte=0), name="reward_stock_nonnegative"
            ),
        ]
        indexes = [
            models.Index(
                fields=["is_active", "points_cost"], name="reward_active_cost_idx"
            )
        ]

    def __str__(self) -> str:
        return self.name


class RewardRedemption(models.Model):
    """Historical audit log of a reward redemption by a user."""

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reward_redemptions",
    )
    reward = models.ForeignKey(
        Reward,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    points_spent = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COMPLETED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "actions_reward_redemption"
        constraints = [
            models.CheckConstraint(
                condition=Q(points_spent__gt=0),
                name="redemption_points_spent_positive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["COMPLETED", "CANCELLED"]),
                name="redemption_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "created_at"], name="redemption_user_created_idx"
            ),
            models.Index(
                fields=["status", "created_at"], name="redemption_status_created_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.reward_id} ({self.points_spent} pts)"
