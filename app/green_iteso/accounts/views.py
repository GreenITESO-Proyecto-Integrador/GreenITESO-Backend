"""DRF views for the accounts domain."""

from __future__ import annotations

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from green_iteso.core.permissions import IsAdmin

from .exceptions import RequestValidationError
from .models import User
from .selectors import get_ecological_profile, get_user_by_id, list_users
from .serializers import (
    EcologicalProfileSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    ProfileUpdateSerializer,
    UserSerializer,
)
from .services import login_with_microsoft, update_profile


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


class LoginView(APIView):
    """Exchange a Microsoft sign-in for a GreenITESO JWT pair."""

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        request=LoginRequestSerializer,
        responses={
            200: LoginResponseSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR"),
            401: OpenApiResponse(description="UNAUTHENTICATED: tokens not verified"),
            403: OpenApiResponse(
                description="DOMAIN_NOT_ALLOWED or PERMISSION_DENIED (inactive account)"
            ),
            429: OpenApiResponse(description="Too many login attempts"),
            503: OpenApiResponse(
                description="SERVER_ERROR: the identity provider is unreachable"
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Verify the Microsoft tokens, create the user if new, and issue JWTs."""
        request_serializer = LoginRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        result = login_with_microsoft(
            id_token=request_serializer.validated_data["id_token"],
            access_token=request_serializer.validated_data["access_token"],
        )
        body = LoginResponseSerializer(
            {
                "access": result.access,
                "refresh": result.refresh,
                "user": result.user,
                "created": result.created,
            }
        )
        return Response(body.data, status=status.HTTP_200_OK)

    def handle_exception(self, exc: Exception) -> Response:
        """Translate DRF's default validation-error shape to the SDD envelope.

        ``is_valid(raise_exception=True)`` raises a plain DRF
        ``ValidationError`` with the framework's own ``{"field": [...]}``
        body; every other error path here already raises a ``LoginError``
        subclass, which already builds ``{"error": {...}}`` itself.
        """
        if isinstance(exc, DRFValidationError):
            exc = RequestValidationError(_flatten_validation_detail(exc.detail))
        return super().handle_exception(exc)


def _flatten_validation_detail(detail: object) -> str:
    """Render DRF's nested field-error structure as one readable message."""
    if isinstance(detail, dict):
        return " | ".join(
            f"{field}: {_flatten_validation_detail(errors)}"
            for field, errors in detail.items()
        )
    if isinstance(detail, list):
        return "; ".join(str(item) for item in detail)
    return str(detail)


class TokenRefreshView(SimpleJWTTokenRefreshView):
    """Exchange a refresh token for a new access token.

    Full refresh-token lifecycle work (rotation, blacklist, logout) is
    T2-11's story; this is simplejwt's stock behavior so the refresh token
    ``LoginView`` already issues is redeemable in the meantime.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"


class EcologicalProfileView(APIView):
    """Return or edit the caller's own aggregated ecological profile (T2-20, T2-21).

    Self-scoped only: this always returns/edits the caller's own data
    regardless of their own ``visibility`` setting (a user always sees and
    manages their own profile). ``visibility`` is exposed here as a field,
    and enforced, if another endpoint is later added to view someone else's
    profile.
    """

    @extend_schema(responses=EcologicalProfileSerializer)
    def get(self, request: Request) -> Response:
        """Aggregate the caller's own data with E1 (points/badges) and E3 (campaigns) data."""
        profile = get_ecological_profile(request.user)
        return Response(EcologicalProfileSerializer(profile).data)

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={
            200: EcologicalProfileSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR"),
        },
    )
    def patch(self, request: Request) -> Response:
        """Edit bio, preferences, visibility, and/or avatar_url (T2-21).

        ``avatar_url`` is the URL of a file the client already uploaded to
        Cloud Storage; this endpoint never receives or stores the binary
        (see ``accounts.serializers._validate_avatar_url`` for why real
        size/type enforcement is still pending the GCS bucket/account).
        """
        payload = ProfileUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        update_profile(user=request.user, **payload.validated_data)
        profile = get_ecological_profile(request.user)
        return Response(EcologicalProfileSerializer(profile).data)
