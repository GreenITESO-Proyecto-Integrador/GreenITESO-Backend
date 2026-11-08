"""Views for the feed module."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.db.models import BooleanField, Count, Exists, OuterRef, QuerySet, Value
from django.http import HttpResponseBase
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from PIL import Image
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from green_iteso.core.image_storage import (
    InvalidImageError,
    image_response,
    save_image,
    validate_image,
)
from green_iteso.core.permissions import IsAdmin
from green_iteso.feed.models import Post, PostLike, PostType
from green_iteso.feed.serializers import PostSerializer

# pylint: disable=no-member

logger = logging.getLogger(__name__)

FEED_IMAGE_PREFIX = "feed"


def _ensure_decodable(uploaded: Any) -> None:
    """Reject a file whose signature is valid but whose body Pillow can't decode."""
    try:
        with Image.open(uploaded) as image:
            image.verify()
    # Pillow raises many exception types for a broken file.
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise InvalidImageError("The image is truncated or corrupt.") from error
    finally:
        uploaded.seek(0)


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
    """ViewSet handling CRUD operations, filtering, and kudos for feed posts."""

    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]

    def get_queryset(self) -> QuerySet[Post]:
        user = self.request.user
        liked_subquery = (
            Exists(PostLike.objects.filter(post=OuterRef("pk"), user_id=user.pk))
            if user and user.is_authenticated
            else Value(False, output_field=BooleanField())
        )

        queryset = Post.objects.select_related(
            "author__profile", "badge_user__badge"
        ).annotate(
            likes_count=Count("likes", distinct=True),
            liked_by_me=liked_subquery,
        )

        post_type = self.request.query_params.get("post_type")
        if post_type in PostType.values:
            queryset = queryset.filter(post_type=post_type)

        ordering = self.request.query_params.get("ordering")
        if ordering == "popular":
            return queryset.order_by("-likes_count", "-created_at")

        return queryset.order_by("-created_at")

    def perform_create(self, serializer: PostSerializer) -> None:
        """Assign authenticated user as author upon creation."""
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=user)

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="PostLikeToggleResponse",
                fields={
                    "liked_by_me": serializers.BooleanField(),
                    "likes_count": serializers.IntegerField(),
                },
            ),
        },
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="like",
    )
    def like(self, request: Request, *_args: Any, **_kwargs: Any) -> Response:
        """Toggle a kudos/like on a feed post for the current user."""
        post = self.get_object()
        deleted_count, _ = PostLike.objects.filter(
            post=post, user=request.user
        ).delete()
        if deleted_count == 0:
            PostLike.objects.get_or_create(post=post, user=request.user)
            liked_by_me = True
        else:
            liked_by_me = False

        likes_count = PostLike.objects.filter(post=post).count()
        return Response(
            {"liked_by_me": liked_by_me, "likes_count": likes_count},
            status=status.HTTP_200_OK,
        )


class ImageUploadView(APIView):
    """Store a feed post image and return the stable URL to put in ``image_url``.

    The URL points at ``FeedImageView`` instead of the bucket. A signed URL
    would expire and is longer than ``Post.image_url`` allows (500).
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "feed_image_upload"

    @extend_schema(
        request={
            "multipart/form-data": inline_serializer(
                name="FeedImageUploadRequest",
                fields={"image": serializers.ImageField()},
            )
        },
        responses={
            200: inline_serializer(
                name="FeedImageUploadResponse", fields={"url": serializers.URLField()}
            ),
            400: OpenApiResponse(description="Missing, invalid, or over 5 MB."),
            500: OpenApiResponse(description="The storage service failed."),
        },
    )
    def post(self, request: Request, *_args: Any, **_kwargs: Any) -> Response:
        """Validate and store the image, then return its URL."""
        file_obj = request.FILES.get("image")
        if not file_obj:
            return Response(
                {"error": "No se proporcionó ninguna imagen."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_image(file_obj)
            _ensure_decodable(file_obj)
            object_key = save_image(FEED_IMAGE_PREFIX, request.user.pk, file_obj)
        except InvalidImageError:
            return Response(
                {
                    "error": "La imagen debe ser un JPG, PNG o WEBP válido de máximo 5 MB."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Could not store a feed image.")
            return Response(
                {"error": "No se pudo guardar la imagen. Inténtalo de nuevo."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        path = reverse(
            "feed-image",
            kwargs={
                "owner_id": request.user.pk,
                "filename": object_key.rsplit("/", 1)[1],
            },
        )
        return Response(
            {"url": request.build_absolute_uri(path)}, status=status.HTTP_200_OK
        )


class FeedImageView(APIView):
    """Serve an image stored by ``ImageUploadView``.

    Open to anyone, like profile avatars: an ``<img>`` can't send the Bearer
    token, and the feed shows these images to every reader. File names are
    random UUIDs, so a URL can't be guessed.
    """

    authentication_classes: list[type] = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        responses={
            (200, "image/*"): OpenApiTypes.BINARY,
            302: OpenApiResponse(description="Redirect to a short-lived signed URL."),
            404: OpenApiResponse(description="No stored image at this URL."),
        },
    )
    def get(self, _request: Request, owner_id: UUID, filename: str) -> HttpResponseBase:
        """Redirect to (deployed) or stream (local) the stored image."""
        object_key = f"{FEED_IMAGE_PREFIX}/{owner_id}/{filename}"
        response = image_response(FEED_IMAGE_PREFIX, owner_id, object_key)
        if response is None:
            raise NotFound()
        # Shorter than the 10-minute signed URL, so a cached redirect never
        # outlives the URL it points at.
        response["Cache-Control"] = "private, max-age=300"
        return response
