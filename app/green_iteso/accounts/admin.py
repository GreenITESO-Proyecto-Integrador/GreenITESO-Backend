from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Clan, ClanMembership, User, UserProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identity", {"fields": ("first_name", "last_name", "firebase_uid", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    search_fields = ("email", "first_name", "last_name")
    filter_horizontal = ("groups", "user_permissions")


@admin.register(Clan)
class ClanAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "privacy", "total_points", "deleted_at")
    list_filter = ("type", "privacy")
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "institutional_clan", "total_points", "current_streak")
    readonly_fields = ("total_points", "current_streak", "last_action_date")


@admin.register(ClanMembership)
class ClanMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "clan", "role", "is_active_private")
    list_filter = ("role", "is_active_private")
