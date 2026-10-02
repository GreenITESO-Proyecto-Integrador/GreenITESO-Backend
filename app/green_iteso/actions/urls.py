"""Router for the actions and exchangeables domain, mounted under /api/v1/."""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from .views import (
    ActionCategoryViewSet,
    ActionMasterViewSet,
    ExchangeableItemViewSet,
)

router = SimpleRouter()
router.register("action-categories", ActionCategoryViewSet, basename="action-category")
router.register("actions", ActionMasterViewSet, basename="action")
router.register("exchangeables", ExchangeableItemViewSet, basename="exchangeable")

urlpatterns = router.urls
