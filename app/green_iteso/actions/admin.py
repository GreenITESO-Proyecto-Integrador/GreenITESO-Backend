from django.contrib import admin

from .models import (
    ActionCategory,
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)


@admin.register(ActionCategory)
class ActionCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(ActionMaster)
class ActionMasterAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "points",
        "daily_limit",
        "validation_mode",
        "is_active",
    )
    list_filter = ("validation_mode", "is_active")
    search_fields = ("code", "name")


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "action", "status", "points_awarded", "created_at")
    list_filter = ("status",)
    search_fields = ("idempotency_key", "user__email")
    readonly_fields = (
        "user",
        "action",
        "institutional_clan",
        "credited_private_clan",
        "campaign",
        "idempotency_key",
        "points_awarded",
        "co2_kg_factor_snapshot",
        "water_liters_factor_snapshot",
        "plastic_kg_factor_snapshot",
        "status",
        "evidence_object_key",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "created_at",
    )


@admin.register(ActionLogMissionContribution)
class ActionLogMissionContributionAdmin(admin.ModelAdmin):
    list_display = ("action_log", "mission", "created_at")
    readonly_fields = ("action_log", "mission", "created_at")
