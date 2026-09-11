from django.contrib import admin

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "scope", "status", "start_date", "end_date", "creator")
    list_filter = ("scope", "status")
    search_fields = ("title",)


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ("campaign", "action", "target_count")


@admin.register(CampaignParticipant)
class CampaignParticipantAdmin(admin.ModelAdmin):
    list_display = ("campaign", "user", "joined_at")


@admin.register(UserMissionProgress)
class UserMissionProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "mission", "current_count", "is_completed")
    readonly_fields = ("current_count", "is_completed", "updated_at")
