from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "notification_type", "is_read", "created_at", "deleted_at")
    list_filter = ("notification_type", "is_read")
    search_fields = ("title", "message", "user__email")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Notification]:
        # Staff can still inspect notifications users have deleted.
        return Notification.all_objects.all()
