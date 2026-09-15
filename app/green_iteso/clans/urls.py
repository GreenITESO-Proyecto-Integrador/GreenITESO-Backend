"""Router for the clans domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .views import ClanViewSet

router = DefaultRouter()
router.register("clans", ClanViewSet, basename="clan")

urlpatterns = router.urls
