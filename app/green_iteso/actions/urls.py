"""Router for the actions domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from .views import ActionCategoryViewSet, ActionMasterViewSet

router = SimpleRouter()
router.register("action-categories", ActionCategoryViewSet, basename="action-category")
router.register("actions", ActionMasterViewSet, basename="action")

urlpatterns = router.urls
