"""DRF views for the accounts domain."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .selectors import get_user_by_id
from .serializers import (
    LoginRequestSerializer,
    LoginResponseSerializer,
    UserSerializer,
)
from .services import login_with_microsoft


class UserViewSet(viewsets.GenericViewSet):
    """Self-scoped access to the caller's own account, under /api/v1/users/."""

    serializer_class = UserSerializer

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
