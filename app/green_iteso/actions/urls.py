"""Router for the actions domain, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    ActionCategoryViewSet,
    ActionLogAuditView,
    ActionLogCreateView,
    ActionMasterViewSet,
    RewardViewSet
)

router = SimpleRouter()
router.register("action-categories", ActionCategoryViewSet, basename="action-category")
router.register("actions", ActionMasterViewSet, basename="action")
router.register("rewards", RewardViewSet, basename="reward")

urlpatterns = router.urls + [
    path("action-logs/", ActionLogCreateView.as_view(), name="action-log-create"),
    path("action-logs/<uuid:log_id>/audit/", ActionLogAuditView.as_view(), name="action-log-audit"),
]
