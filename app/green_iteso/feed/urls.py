"""URL routing for the feed module."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from green_iteso.feed.views import PostViewSet

app_name = "feed"

router = DefaultRouter()
router.register(r"", PostViewSet, basename="post")

urlpatterns = router.urls
