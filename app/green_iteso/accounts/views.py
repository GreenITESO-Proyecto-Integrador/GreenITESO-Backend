"""DRF views for the accounts domain."""

from __future__ import annotations

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .selectors import get_user_by_id
from .serializers import UserSerializer


class UserViewSet(viewsets.GenericViewSet):
    """Self-scoped access to the caller's own account, under /api/v1/users/."""

    serializer_class = UserSerializer

    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        """Return the authenticated caller's own account."""
        user = get_user_by_id(request.user.pk)
        serializer = self.get_serializer(user)
        return Response(serializer.data)
