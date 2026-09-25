"""Router for the actions catalog, mounted under /api/v1/.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-18
"""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from .views import ActionCategoryViewSet, ActionMasterViewSet

router = SimpleRouter()
# Register the nested prefix first so it is never shadowed by actions/<pk>/.
router.register("actions/categories", ActionCategoryViewSet, basename="action-category")
router.register("actions", ActionMasterViewSet, basename="action")

urlpatterns = router.urls
