"""Provisional campaign and mission tables needed by the audit graph."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Campaign(models.Model):
    class Scope(models.TextChoices):
        GLOBAL = "GLOBAL", "Global"
        PRIVATE = "PRIVATE", "Private clan"

    class Status(models.TextChoices):
        PROMOTION = "PROMOTION", "Promotion"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        FINISHED = "FINISHED", "Finished"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROMOTION
    )
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_campaigns",
    )
    target_clan = models.ForeignKey(
        "accounts.Clan",
        on_delete=models.PROTECT,
        related_name="target_campaigns",
        null=True,
        blank=True,
    )
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    # P8 remains unanswered; this nullable field reserves the proposed snapshot shape.
    podium_snapshot = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")),
                name="campaign_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    Q(scope="GLOBAL", target_clan__isnull=True)
                    | Q(scope="PRIVATE", target_clan__isnull=False)
                ),
                name="campaign_scope_target_clan_consistent",
            ),
            models.CheckConstraint(
                condition=Q(scope__in=["GLOBAL", "PRIVATE"]),
                name="campaign_scope_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["PROMOTION", "IN_PROGRESS", "FINISHED"]),
                name="campaign_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "start_date", "end_date"], name="campaign_window_idx"
            )
        ]

    def __str__(self) -> str:
        return self.title


class Mission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.PROTECT, related_name="missions"
    )
    action = models.ForeignKey(
        "actions.ActionMaster",
        on_delete=models.PROTECT,
        related_name="missions",
        db_column="action_master_id",
    )
    target_count = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(target_count__gt=0), name="mission_target_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.campaign.title}: {self.action.code}"


class CampaignParticipant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.PROTECT, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="campaign_participations",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user"], name="campaign_participant_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.campaign.title}: {self.user.email}"


class UserMissionProgress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mission_progress",
    )
    mission = models.ForeignKey(
        Mission, on_delete=models.PROTECT, related_name="user_progress"
    )
    current_count = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "mission"], name="user_mission_progress_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.email}: {self.mission_id}"
