"""Views for the feed module."""

from __future__ import annotations

import os
from typing import Any
import uuid

from django.conf import settings
from django.db.models import QuerySet
from google.cloud import storage
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.feed.models import Post
from green_iteso.feed.serializers import PostSerializer


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Custom permission to restrict update and delete actions."""

    def has_object_permission(self, request: Request, view: Any, obj: Post) -> bool:
        """Allow read access to all, updates to author or staff."""
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
        return (
            Post.objects.select_related("author", "badge_user__badge")
            .all()
            .order_by("-created_at")
        )

    def perform_create(self, serializer: PostSerializer) -> None:
        """Assign authenticated user as author upon creation."""
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=user)

    def perform_destroy(self, instance: Post) -> None:
        """Ensure only the post author or staff members can delete the instance."""
        if instance.author != self.request.user and not self.request.user.is_staff:
            raise PermissionDenied("You can only delete your own posts.")
        instance.delete()


class ImageUploadView(APIView):
    """Endpoint handling multipart image uploads to Google Cloud Storage."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        file_obj = request.FILES.get("image")
        if not file_obj:
            return Response(
                {"error": "No se proporcionó ningún archivo en el campo 'image'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        if file_obj.content_type not in allowed_types:
            return Response(
                {"error": "Formato no permitido. Usa imágenes JPG, PNG o WebP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            client = storage.Client()
            bucket_name = getattr(
                settings,
                "GCS_BUCKET_NAME",
                os.getenv("GCS_BUCKET_NAME", "greeniteso-production-objects"),
            )
            bucket = client.bucket(bucket_name)

            ext = file_obj.name.split(".")[-1].lower() if "." in file_obj.name else "jpg"
            blob_name = f"uploads/{uuid.uuid4()}.{ext}"
            blob = bucket.blob(blob_name)

            file_obj.seek(0)
            blob.upload_from_file(file_obj, content_type=file_obj.content_type)

            public_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"
            return Response({"url": public_url}, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"error": f"Error al subir a almacenamiento en la nube: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
