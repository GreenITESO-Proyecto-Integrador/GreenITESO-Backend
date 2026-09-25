"""Read-only queries for the actions catalog.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-18
"""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet

from .models import ActionCategory, ActionMaster


def list_active_actions() -> QuerySet[ActionMaster]:
    """Return active catalog actions with their category, ordered for a stable listing."""
    return (
        ActionMaster.objects.filter(is_active=True)
        .select_related("category")
        .order_by("category__name", "name", "code")
    )


def list_categories_with_active_actions() -> QuerySet[ActionCategory]:
    """Return every category with only its active actions prefetched as `active_actions`.

    Categories without active actions are still returned so the frontend can render
    the full taxonomy; their `active_actions` list is empty.
    """
    return ActionCategory.objects.prefetch_related(
        Prefetch(
            "actions",
            queryset=ActionMaster.objects.filter(is_active=True).order_by(
                "name", "code"
            ),
            to_attr="active_actions",
        )
    ).order_by("name", "code")
