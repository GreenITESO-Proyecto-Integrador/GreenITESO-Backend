"""DRF views for the accounts domain."""

from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet
from django.http import HttpResponseBase
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import (
    TokenBlacklistView as SimpleJWTTokenBlacklistView,
)
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from green_iteso.core.image_storage import InvalidImageError, image_response
from green_iteso.core.permissions import IsAdmin
from green_iteso.core.throttling import (
    AuthIPCeilingThrottle,
    FirstRejectionThrottleMixin,
    LoginIdentityThrottle,
    RefreshTokenIdentityThrottle,
)

from .avatars import PROFILE_AVATAR_PREFIX
from .exceptions import RequestValidationError
from .models import User, UserProfile
from .selectors import (
    MetricsFilters,
    get_ecological_profile,
    get_impact_trend,
    get_profile_metrics,
    get_user_by_id,
    list_users,
)
from .serializers import (
    ChangeRoleRequestSerializer,
    EcologicalProfileSerializer,
    ImpactTrendPointSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    NicknameChangeResultSerializer,
    NicknameChangeSerializer,
    ProfileAvatarUploadSerializer,
    ProfileMetricsQuerySerializer,
    ProfileMetricsSerializer,
    ProfileUpdateSerializer,
    UserRoleAuditSerializer,
    UserSerializer,
)
from .services import (
    InsufficientPointsForNicknameError,
    NicknameUnchangedError,
    change_nickname,
    login_with_microsoft,
    set_profile_avatar,
    update_profile,
    update_user_role,
)


class UserViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Self-scoped `me`, plus an admin-only directory listing, under /api/v1/users/."""

    serializer_class = UserSerializer

    def get_queryset(self) -> QuerySet[User]:
        return list_users()

    def get_permissions(self) -> list[BasePermission]:
        if self.action in {"list", "role", "role_history"}:
            return [IsAdmin()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        """Return the authenticated caller's own account."""
        user = get_user_by_id(request.user.pk)
        serializer = self.get_serializer(user)
        return Response(serializer.data)

    @extend_schema(
        request=ChangeRoleRequestSerializer,
        responses={
            200: UserSerializer,
            400: OpenApiResponse(
                description="VALIDATION_ERROR: invalid role or last admin check failed"
            ),
            401: OpenApiResponse(description="UNAUTHENTICATED"),
            403: OpenApiResponse(description="PERMISSION_DENIED: caller is not ADMIN"),
            404: OpenApiResponse(description="NOT_FOUND: user not found"),
        },
    )
    @action(detail=True, methods=["patch"], url_path="role")
    def role(  # pylint: disable=unused-argument
        self, request: Request, pk: str | None = None
    ) -> Response:
        """Update a user's global role (ADMIN only)."""
        target_user = self.get_object()
        serializer = ChangeRoleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = update_user_role(
            admin_user=request.user,
            user=target_user,
            new_role=serializer.validated_data["role"],
        )
        return Response(UserSerializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={
            200: UserRoleAuditSerializer(many=True),
            401: OpenApiResponse(description="UNAUTHENTICATED"),
            403: OpenApiResponse(description="PERMISSION_DENIED: caller is not ADMIN"),
            404: OpenApiResponse(description="NOT_FOUND: user not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="role-history")
    def role_history(  # pylint: disable=unused-argument
        self, request: Request, pk: str | None = None
    ) -> Response:
        """List audit history for a user's role changes (ADMIN only)."""
        target_user = self.get_object()
        audits = target_user.role_audit_logs.all()
        page = self.paginate_queryset(audits)
        if page is not None:
            serializer = UserRoleAuditSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(UserRoleAuditSerializer(audits, many=True).data)


class LoginView(FirstRejectionThrottleMixin, APIView):
    """Exchange a Microsoft sign-in for a GreenITESO JWT pair."""

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginIdentityThrottle, AuthIPCeilingThrottle]
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
            },
            context={"request": request},
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


class TokenRefreshView(FirstRejectionThrottleMixin, SimpleJWTTokenRefreshView):
    """Exchange a refresh token for a new access token (T2-11).

    ``ROTATE_REFRESH_TOKENS``/``BLACKLIST_AFTER_ROTATION`` make this also
    return a new refresh token and blacklist the one just redeemed, so each
    refresh token is single-use.
    """

    throttle_classes = [RefreshTokenIdentityThrottle, AuthIPCeilingThrottle]
    throttle_scope = "auth_refresh"


class LogoutView(FirstRejectionThrottleMixin, SimpleJWTTokenBlacklistView):
    """Blacklist a refresh token so it can no longer be redeemed (T2-11).

    Subclasses simplejwt's stock ``TokenBlacklistView`` (same relationship
    ``TokenRefreshView`` above has to its own simplejwt base): it takes no
    access token, so a client whose access token already expired can still
    log out, and its ``get_authenticate_header`` override keeps an invalid
    refresh token's ``AuthenticationFailed`` at 401 even though the view has
    no real authenticator to challenge with (DRF otherwise downgrades that
    to 403).
    """

    throttle_classes = [RefreshTokenIdentityThrottle, AuthIPCeilingThrottle]
    throttle_scope = "auth_logout"


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
        return Response(
            EcologicalProfileSerializer(profile, context={"request": request}).data
        )

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={
            200: EcologicalProfileSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR"),
        },
    )
    def patch(self, request: Request) -> Response:
        """Edit bio, preferences, visibility, and/or avatar_url (T2-21).

        ``avatar_url`` is an external https link to a photo. Setting it
        replaces a photo uploaded through ``/profile/me/avatar/``; uploading
        a file from the device goes through that endpoint instead (#170).
        """
        payload = ProfileUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        update_profile(user=request.user, **payload.validated_data)
        profile = get_ecological_profile(request.user)
        return Response(
            EcologicalProfileSerializer(profile, context={"request": request}).data
        )


class ProfileAvatarView(APIView):
    """Upload or remove the caller's own profile photo (#170)."""

    parser_classes = [MultiPartParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "profile_avatar"

    @extend_schema(
        request={"multipart/form-data": ProfileAvatarUploadSerializer},
        responses={
            200: EcologicalProfileSerializer,
            400: OpenApiResponse(description="Not a JPG/PNG/WEBP of at most 5 MB."),
        },
    )
    def post(self, request: Request) -> Response:
        """Store a JPG/PNG/WEBP of at most 5 MB as the photo, replacing any link."""
        payload = ProfileAvatarUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._set_avatar(request, payload.validated_data["avatar"])

    @extend_schema(request=None, responses={200: EcologicalProfileSerializer})
    def delete(self, request: Request) -> Response:
        """Remove the photo, whether it was uploaded or a pasted link."""
        return self._set_avatar(request, None)

    def _set_avatar(self, request: Request, uploaded: object) -> Response:
        try:
            set_profile_avatar(user=request.user, uploaded=uploaded)
        except InvalidImageError as exc:
            raise DRFValidationError({"avatar": [str(exc)]}) from exc
        profile = get_ecological_profile(request.user)
        return Response(
            EcologicalProfileSerializer(profile, context={"request": request}).data
        )


class ProfileAvatarImageView(APIView):
    """Serve a user's uploaded profile photo (#170).

    Open to anyone: an ``<img>`` can't send the Bearer token, and the photo
    is shown wherever the user appears (feed, clans, rankings). It only
    serves files uploaded through ``ProfileAvatarView``, never a pasted link.
    """

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            (200, "image/*"): OpenApiTypes.BINARY,
            302: OpenApiResponse(description="Redirect to a short-lived signed URL."),
            404: OpenApiResponse(description="The user has no uploaded photo."),
        },
    )
    def get(self, _request: Request, user_id: UUID) -> HttpResponseBase:
        """Redirect to (deployed) or stream (local) the stored photo."""
        object_key = (
            UserProfile.objects.filter(user_id=user_id, user__is_active=True)
            .values_list("avatar_object_key", flat=True)
            .first()
        )
        response = image_response(PROFILE_AVATAR_PREFIX, user_id, object_key or "")
        if response is None:
            raise NotFound()
        response["Cache-Control"] = "private, max-age=300"
        return response


class ProfileNicknameView(APIView):
    """Change the caller's nickname: the first change is free, later ones cost points (#175)."""

    @extend_schema(
        request=NicknameChangeSerializer,
        responses={
            200: NicknameChangeResultSerializer,
            400: OpenApiResponse(
                description="VALIDATION_ERROR: invalid, unchanged, or not enough points"
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Set the nickname, charging ``available_points`` after the first change."""
        payload = NicknameChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            result = change_nickname(
                user=request.user, nickname=payload.validated_data["nickname"]
            )
        except (NicknameUnchangedError, InsufficientPointsForNicknameError) as exc:
            raise DRFValidationError({"nickname": str(exc)}) from exc
        return Response(NicknameChangeResultSerializer(result).data)


class ProfileMetricsView(APIView):
    """Return the caller's own ecological-profile metrics (#126).

    Self-scoped like ``EcologicalProfileView``: only the caller's own data is
    read, so there is no user id to pass.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "from",
                OpenApiTypes.DATE,
                description="First local day (America/Mexico_City) included.",
            ),
            OpenApiParameter(
                "to",
                OpenApiTypes.DATE,
                description="Last local day (America/Mexico_City) included.",
            ),
            OpenApiParameter(
                "category",
                OpenApiTypes.STR,
                description="ActionCategory code; narrows action-derived metrics only.",
            ),
            OpenApiParameter(
                "granularity",
                OpenApiTypes.STR,
                enum=["week", "month"],
                description="Bucket size of `activity` (default `week`).",
            ),
        ],
        responses={
            200: ProfileMetricsSerializer,
            400: OpenApiResponse(description="VALIDATION_ERROR"),
        },
    )
    def get(self, request: Request) -> Response:
        """Aggregate impact, actions, points, activity, badges and campaigns."""
        query = ProfileMetricsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        category = params.get("category")
        filters = MetricsFilters(
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
            category=category.code if category is not None else None,
            granularity=params["granularity"],
        )
        metrics = get_profile_metrics(request.user, filters)
        return Response(ProfileMetricsSerializer(metrics).data)


class ImpactTrendView(APIView):
    """Caller's own weekly impact totals for the last 4 ISO weeks.

    Self-scoped only, same as ``EcologicalProfileView``. Feeds a dashboard
    trend chart: a fixed-size array (one entry per week, zero-filled) rather
    than a paginated collection, since the client always wants exactly
    ``IMPACT_TREND_WEEKS`` points to plot.
    """

    @extend_schema(responses=ImpactTrendPointSerializer(many=True))
    def get(self, request: Request) -> Response:
        """Return the last 4 weeks of approved-action impact, oldest first."""
        points = get_impact_trend(request.user)
        return Response(ImpactTrendPointSerializer(points, many=True).data)
