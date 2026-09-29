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
from rest_framework_simplejwt.views import (
    TokenBlacklistView as SimpleJWTTokenBlacklistView,
)
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from green_iteso.core.permissions import IsAdmin

from .exceptions import RequestValidationError
from .models import User
from .selectors import get_user_by_id, list_users
from .serializers import (
    LoginRequestSerializer,
    LoginResponseSerializer,
    UserSerializer,
)
from .services import login_with_microsoft


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
    """Exchange a refresh token for a new access token (T2-11).

    ``ROTATE_REFRESH_TOKENS``/``BLACKLIST_AFTER_ROTATION`` make this also
    return a new refresh token and blacklist the one just redeemed, so each
    refresh token is single-use.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"


class LogoutView(SimpleJWTTokenBlacklistView):
    """Blacklist a refresh token so it can no longer be redeemed (T2-11).

    Subclasses simplejwt's stock ``TokenBlacklistView`` (same relationship
    ``TokenRefreshView`` above has to its own simplejwt base): it takes no
    access token, so a client whose access token already expired can still
    log out, and its ``get_authenticate_header`` override keeps an invalid
    refresh token's ``AuthenticationFailed`` at 401 even though the view has
    no real authenticator to challenge with (DRF otherwise downgrades that
    to 403).
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_logout"
