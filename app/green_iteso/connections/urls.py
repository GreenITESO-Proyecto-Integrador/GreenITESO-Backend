"""Router for the connections (friendships) domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from .views import FriendshipViewSet

router = SimpleRouter()
router.register("friendships", FriendshipViewSet, basename="friendship")

urlpatterns = [
    *router.urls,
]
