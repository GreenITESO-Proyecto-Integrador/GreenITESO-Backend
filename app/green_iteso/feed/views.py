"""Views for the feed module."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request

from green_iteso.feed.models import Post
from green_iteso.feed.serializers import PostSerializer


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Custom permission to restrict update and delete actions."""

    def has_object_permission(self, request: Request, view: Any, obj: Post) -> bool:
        """Allow read access to all, updates to author, deletes to author or staff."""
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.method == "DELETE" and request.user and request.user.is_staff:
            return True
        return obj.author == request.user


class PostViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """ViewSet handling CRUD operations for community feed posts."""

    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]

    def get_queryset(self) -> QuerySet[Post]:
        """Optimize database query with select_related for author profile data."""
        return Post.objects.select_related("author").all()

    def perform_create(self, serializer: PostSerializer) -> None:
        """Assign authenticated user as author upon creation."""
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=user)

    def perform_destroy(self, instance: Post) -> None:
        """Ensure only the post author or staff members can delete the instance."""
        if instance.author != self.request.user and not self.request.user.is_staff:
            raise PermissionDenied("You can only delete your own posts.")
        instance.delete()
