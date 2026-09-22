from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Clan, ClanMembership, User, UserProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "nickname", "role", "is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Identity",
            {"fields": ("first_name", "last_name", "nickname", "firebase_uid", "role")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
    search_fields = ("email", "first_name", "last_name")
    filter_horizontal = ("groups", "user_permissions")


@admin.register(Clan)
class ClanAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "privacy", "total_points", "deleted_at")
    list_filter = ("type", "privacy")
    search_fields = ("name",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Clan]:
        # Swap in the unfiltered manager so soft-deleted clans stay visible in
        # admin, but otherwise mirror ModelAdmin.get_queryset's own ordering
        # step so a future `ordering`/list_select_related change on this
        # class is not silently dropped by this override.
        queryset = Clan.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "institutional_clan",
        "total_points",
        "available_points",
        "current_streak",
    )
    readonly_fields = (
        "total_points",
        "available_points",
        "current_streak",
        "last_action_date",
    )


class ClanMembershipAdminForm(forms.ModelForm):
    """Enforce the single-LEADER-per-clan rule on direct admin writes.

    ``clans.services.assign_leader`` guards the normal application write path,
    but Django admin saves a ``ClanMembership`` via ``ModelForm`` directly,
    bypassing it. This form closes that gap for both add and change.
    """

    class Meta:
        model = ClanMembership
        fields = ["user", "clan", "role", "is_active_private"]

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        clan = cleaned_data.get("clan")
        if role == ClanMembership.MembershipRole.LEADER and clan is not None:
            existing_leader = (
                ClanMembership.objects.filter(
                    clan=clan, role=ClanMembership.MembershipRole.LEADER
                )
                .exclude(pk=self.instance.pk)
                .first()
            )
            if existing_leader is not None:
                raise forms.ValidationError(
                    f"Clan {clan} already has a LEADER membership ({existing_leader.user})."
                )
        return cleaned_data


@admin.register(ClanMembership)
class ClanMembershipAdmin(admin.ModelAdmin):
    form = ClanMembershipAdminForm
    list_display = ("user", "clan", "role", "is_active_private")
    list_filter = ("role", "is_active_private")
