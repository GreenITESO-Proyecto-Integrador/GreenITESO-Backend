"""Views for the feed module."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError
from PIL import Image, UnidentifiedImageError
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.core.permissions import IsAdmin
from green_iteso.feed.models import Post
from green_iteso.feed.serializers import PostSerializer

# pylint: disable=no-member

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def detect_image_mime(file_obj: Any) -> str | None:
    """Validate image magic bytes and return canonical MIME type."""
    header = file_obj.read(12)
    file_obj.seek(0)
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Custom permission to restrict update and delete actions."""

    def has_object_permission(self, request: Request, view: Any, obj: Post) -> bool:
        """Allow read access to all, updates to author, deletes to author or admin."""
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.method == "DELETE" and request.user:
            return obj.author == request.user or IsAdmin().has_permission(request, view)
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
        """Ensure only the post author or admins can delete the instance."""
        if instance.author != self.request.user and not IsAdmin().has_permission(self.request, self):
            raise PermissionDenied("You can only delete your own posts.")
        instance.delete()


class ImageUploadView(APIView):
    """Endpoint handling multipart image uploads to Google Cloud Storage."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request, *_args: Any, **_kwargs: Any) -> Response:  # pylint: disable=too-many-return-statements
        """Process the image upload and store it securely in Google Cloud Storage."""
        bucket_name = getattr(settings, "GCS_EVIDENCE_BUCKET", None)
        if not bucket_name:
            logger.error("GCS_EVIDENCE_BUCKET is not configured in Django settings.")
            return Response(
                {"error": "El servicio de almacenamiento no está configurado."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        file_obj = request.FILES.get("image")
        if not file_obj:
            return Response(
                {"error": "No se proporcionó ningún archivo en el campo 'image'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if file_obj.size > MAX_FILE_SIZE_BYTES:
            return Response(
                {"error": "El archivo excede el tamaño máximo permitido de 5 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        detected_mime = detect_image_mime(file_obj)
        if not detected_mime or detected_mime not in MIME_TO_EXT:
            err_msg = (
                "Formato no permitido o corrupto. Usa imágenes JPG, PNG o WebP válidas."
            )
            return Response({"error": err_msg}, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_obj.seek(0)
            with Image.open(file_obj) as img:
                img.verify()
        except (UnidentifiedImageError, Exception):
            return Response(
                {"error": "La imagen está truncada o no se puede decodificar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = MIME_TO_EXT[detected_mime]
        blob_name = f"uploads/{uuid.uuid4()}.{ext}"

        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            file_obj.seek(0)
            blob.upload_from_file(file_obj, content_type=detected_mime)

            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=1),
                method="GET",
            )
            return Response({"url": signed_url}, status=status.HTTP_200_OK)
        except GoogleCloudError:
            logger.exception("Google Cloud Storage failure during image upload.")
            return Response(
                {
                    "error": "Error al comunicar con el servicio de almacenamiento en la nube."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected error processing image upload.")
            return Response(
                {"error": "Ocurrió un error inesperado al procesar la imagen."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
