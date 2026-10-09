from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="list"),
    path(
        "unread-count/",
        views.NotificationUnreadCountView.as_view(),
        name="unread-count",
    ),
    path(
        "mark-all-read/",
        views.NotificationMarkAllReadView.as_view(),
        name="mark-all-read",
    ),
    path("<uuid:pk>/", views.NotificationDetailView.as_view(), name="detail"),
]
