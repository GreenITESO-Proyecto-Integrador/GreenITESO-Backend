from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification
from .serializers import NotificationSerializer

_TRUE_VALUES = {"true", "1"}
_FALSE_VALUES = {"false", "0"}


class NotificationListView(generics.ListAPIView):
    """List the current user's notifications, newest first."""

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[Notification]:
        queryset = Notification.objects.filter(user=self.request.user)
        is_read_param = self.request.query_params.get("is_read")
        if is_read_param is not None:
            lowered = is_read_param.strip().lower()
            if lowered in _TRUE_VALUES:
                queryset = queryset.filter(is_read=True)
            elif lowered in _FALSE_VALUES:
                queryset = queryset.filter(is_read=False)
        return queryset

    def list(self, request: Request, *args: object, **kwargs: object) -> Response:
        response = super().list(request, *args, **kwargs)
        response.data["unread_count"] = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return response


class NotificationUnreadCountView(APIView):
    """Report only the count of unread notifications for the current user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        unread_count = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return Response({"unread_count": unread_count})


class NotificationDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Owner-only read/patch/delete of a single notification."""

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["patch", "delete", "options"]

    def get_queryset(self) -> QuerySet[Notification]:
        return Notification.objects.filter(user=self.request.user)


class NotificationMarkAllReadView(APIView):
    """Mark every unread notification of the current user as read."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request: Request) -> Response:
        marked_as_read = Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True)
        return Response({"marked_as_read": marked_as_read}, status=status.HTTP_200_OK)
