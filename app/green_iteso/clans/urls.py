"""Router for the clans domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from .views import ClanViewSet

router = SimpleRouter()
router.register("clans", ClanViewSet, basename="clan")

urlpatterns = router.urls
