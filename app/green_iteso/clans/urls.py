"""Router for the clans domain, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import ClanViewSet, InstitutionalClanAssignmentView

router = SimpleRouter()
router.register("clans", ClanViewSet, basename="clan")

urlpatterns = [
    # Must precede the router's /clans/<pk>/ pattern, which would otherwise
    # swallow this static path first.
    path(
        "clans/institutional-clan/",
        InstitutionalClanAssignmentView.as_view(),
        name="institutional-clan-assignment",
    ),
    *router.urls,
]
