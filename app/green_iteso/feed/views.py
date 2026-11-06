"""Views for the feed module."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import QuerySet
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError
from PIL import Image, UnidentifiedImageError
from rest_framework import permissions, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.actions.evidence import _signing_credentials, gcs_evidence_enabled
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
            Post.objects.select_related("author__profile", "badge_user__badge")
            .all()
            .order_by("-created_at")
        )

    def perform_create(self, serializer: PostSerializer) -> None:
        """Assign authenticated user as author upon creation."""
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=user)


class ImageUploadView(APIView):
    """Endpoint handling multipart image uploads to Google Cloud Storage."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request, *_args: Any, **_kwargs: Any) -> Response:  # pylint: disable=too-many-return-statements,too-many-locals
        """Process the image upload and store it securely."""
        file_obj = request.FILES.get("image")
        if not file_obj:
            return Response(
                {"error": "No se proporcionó ninguna imagen."}, 
                status=status.HTTP_400_BAD_REQUEST,
            )

        if file_obj.size > MAX_FILE_SIZE_BYTES:
            return Response(
                {"error": "La evidencia no puede superar 5 MB."}, 
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            detected_mime = Image.open(file_obj).format.lower()
            if MIME_TO_EXT.get(f"image/{detected_mime}") not in MIME_TO_EXT.values():
                raise UnidentifiedImageError
        except (UnidentifiedImageError, OSError):
            return Response(
                {"error": "La imagen está truncada o no se puede decodificar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = MIME_TO_EXT.get(f"image/{detected_mime}", "jpg")
        blob_name = f"uploads/{uuid.uuid4()}.{ext}"
        file_obj.seek(0)

        # 1. FLUJO LOCAL: Guardar en disco si no estamos en producción
        if not gcs_evidence_enabled():
            path = default_storage.save(blob_name, file_obj)
            local_url = request.build_absolute_uri(default_storage.url(path))
            return Response({"url": local_url}, status=status.HTTP_200_OK)

        # 2. FLUJO NUBE (Cloud Run): Lo que ya tienes implementado
        try:
            client = storage.Client()
            bucket = client.bucket(settings.GCS_EVIDENCE_BUCKET)
            blob = bucket.blob(blob_name)

            file_obj.seek(0)
            blob.upload_from_file(file_obj, content_type=f"image/{detected_mime}")

            email, token = _signing_credentials()

            if not email or not token:
                logger.error("No se pudieron obtener credenciales de Cloud Run para firmar la URL.")
                return Response(
                    {"error": "Error interno de permisos en la nube."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(days=7),
                method="GET",
                service_account_email=email,
                access_token=token,
            )

            return Response({"url": signed_url}, status=status.HTTP_200_OK)

        except GoogleCloudError:
            logger.exception("Google Cloud Storage failure during image upload.")
            return Response(
                {"error": "Error al comunicar con el servicio de almacenamiento en la nube."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected error processing image upload.")
            return Response(
                {"error": "Ocurrió un error inesperado al procesar la imagen."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
