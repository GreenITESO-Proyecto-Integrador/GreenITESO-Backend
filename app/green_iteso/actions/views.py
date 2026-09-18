"""DRF views for the actions domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import mixins, viewsets

from .models import ActionCategory, ActionMaster
from .selectors import list_active_action_categories, list_active_actions
from .serializers import ActionCategorySerializer, ActionMasterSerializer


class ActionCategoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve action categories under /api/v1/action-categories/."""

    serializer_class = ActionCategorySerializer

    def get_queryset(self) -> QuerySet[ActionCategory]:
        return list_active_action_categories()


class ActionMasterViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve action definitions under /api/v1/actions/."""

    serializer_class = ActionMasterSerializer

    def get_queryset(self) -> QuerySet[ActionMaster]:
        return list_active_actions()
