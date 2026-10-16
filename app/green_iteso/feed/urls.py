"""URL routing for the feed module."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from green_iteso.feed.views import ImageUploadView, PostViewSet

app_name = "feed"

router = DefaultRouter()
router.register(r"", PostViewSet, basename="post")

urlpatterns = [
    path("uploads/", ImageUploadView.as_view(), name="image-upload"),
    *router.urls,
]
