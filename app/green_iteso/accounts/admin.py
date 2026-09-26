from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Clan, ClanMembership, Friendship, User, UserProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "nickname", "role", "is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Identity",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "nickname",
                    "firebase_uid",
                    "microsoft_oid",
                    "role",
                )
            },
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
    """Give a friendly error for the common (non-concurrent) case.

    ``clans.services.assign_leader`` guards the normal application write path,
    but Django admin saves a ``ClanMembership`` via ``ModelForm`` directly,
    bypassing it. This check runs outside any lock, so it cannot rule out a
    race between two concurrent admin submissions on its own -- see
    ``ClanMembershipAdmin.save_model`` for the guard that actually closes
    that race.
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

    def save_model(
        self,
        request: HttpRequest,
        obj: ClanMembership,
        form: ClanMembershipAdminForm,
        change: bool,
    ) -> None:
        """Re-check the single-LEADER rule atomically, under a clan-row lock.

        ``ClanMembershipAdminForm.clean()`` already rejects the common case,
        but it runs unlocked before the save, so two concurrent admin
        submissions can both pass it and both write a LEADER row -- the same
        race ``clans.services.assign_leader`` closes on the normal write
        path. Lock the clan row and re-check immediately before the write,
        inside the same transaction, so the two requests serialize instead.

        A conflict detected here raises past Django admin's normal
        validation-error handling (which only wraps ``form.is_valid()``), so
        it surfaces as a 500 rather than a re-rendered form. The write is
        still rolled back by the transaction either way; only the race case
        gets the worse error page instead of a friendly message.
        """
        with transaction.atomic():
            if obj.role == ClanMembership.MembershipRole.LEADER:
                Clan.objects.select_for_update().get(pk=obj.clan_id)
                existing_leader = (
                    ClanMembership.objects.filter(
                        clan_id=obj.clan_id,
                        role=ClanMembership.MembershipRole.LEADER,
                    )
                    .exclude(pk=obj.pk)
                    .first()
                )
                if existing_leader is not None:
                    raise ValidationError(
                        f"Clan {obj.clan} already has a LEADER membership "
                        f"({existing_leader.user})."
                    )
            super().save_model(request, obj, form, change)


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("requester", "addressee", "status", "created_at", "responded_at")
    list_filter = ("status",)
    search_fields = ("requester__email", "addressee__email")
    readonly_fields = ("low_user", "high_user", "created_at")
