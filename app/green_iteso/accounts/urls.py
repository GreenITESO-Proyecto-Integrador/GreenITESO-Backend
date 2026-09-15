"""Router for the accounts domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .views import UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = router.urls
