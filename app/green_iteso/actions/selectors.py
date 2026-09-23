"""DRF selectors for the actions domain."""

from __future__ import annotations

from django.db.models import QuerySet

from .models import ActionCategory, ActionMaster


def list_active_action_categories() -> QuerySet[ActionCategory]:
    """Return action categories ordered by code."""
    return ActionCategory.objects.all().order_by("code")


def list_active_actions() -> QuerySet[ActionMaster]:
    """Return active actions ordered by code."""
    return ActionMaster.objects.filter(is_active=True).order_by("code")
