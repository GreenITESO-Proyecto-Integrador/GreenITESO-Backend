"""DRF views for the accounts domain."""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response

from green_iteso.core.permissions import IsAdmin

from .models import User
from .selectors import get_user_by_id, list_users
from .serializers import UserSerializer


class UserViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Self-scoped `me`, plus an admin-only directory listing, under /api/v1/users/."""

    serializer_class = UserSerializer

    def get_queryset(self) -> QuerySet[User]:
        return list_users()

    def get_permissions(self) -> list[BasePermission]:
        if self.action == "list":
            return [IsAdmin()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        """Return the authenticated caller's own account."""
        user = get_user_by_id(request.user.pk)
        serializer = self.get_serializer(user)
        return Response(serializer.data)
