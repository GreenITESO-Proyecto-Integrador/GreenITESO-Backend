"""Views for feedback submissions."""

from __future__ import annotations

from uuid import UUID

from django.http import HttpResponseBase
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.core.image_storage import image_response
from green_iteso.core.permissions import IsAdmin

from .models import FeedbackSubmission
from .serializers import (
    FeedbackAdminUpdateSerializer,
    FeedbackCreateSerializer,
    FeedbackDetailSerializer,
)
from .services import (
    FEEDBACK_IMAGE_PREFIX,
    create_feedback_submission,
)
from .throttles import FeedbackSubmitThrottle


class FeedbackPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class FeedbackListCreateView(APIView):
    """Submit feedback (any authenticated user) or list all feedback (Admin only)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_throttles(self) -> list:
        if self.request.method == "POST":
            return [FeedbackSubmitThrottle()]
        return []

    def post(self, request: Request) -> Response:
        serializer = FeedbackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        screenshot = validated.get("screenshot")
        submission = create_feedback_submission(
            user=request.user,
            category=validated.get("category", FeedbackSubmission.Category.OTHER),
            subject=validated["subject"],
            message=validated["message"],
            rating=validated.get("rating"),
            screenshot=screenshot,
        )
        return Response(
            FeedbackDetailSerializer(submission).data,
            status=status.HTTP_201_CREATED,
        )

    def get(self, request: Request) -> Response:
        """Admin-only list of all feedback submissions."""
        if not IsAdmin().has_permission(request, self):
            raise PermissionDenied("Only administrators can view all feedback.")

        queryset = FeedbackSubmission.objects.select_related("user").all()
        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        paginator = FeedbackPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = FeedbackDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class MyFeedbackListView(APIView):
    """List feedback submitted by the authenticated user."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        queryset = FeedbackSubmission.objects.filter(user=request.user)
        paginator = FeedbackPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = FeedbackDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FeedbackDetailView(APIView):
    """Admin-only detail and update view."""

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, _request: Request, submission_id: UUID) -> Response:
        try:
            submission = FeedbackSubmission.objects.select_related("user").get(
                pk=submission_id
            )
        except FeedbackSubmission.DoesNotExist:
            raise NotFound() from None
        return Response(FeedbackDetailSerializer(submission).data)

    def patch(self, request: Request, submission_id: UUID) -> Response:
        try:
            submission = FeedbackSubmission.objects.get(pk=submission_id)
        except FeedbackSubmission.DoesNotExist:
            raise NotFound() from None

        serializer = FeedbackAdminUpdateSerializer(
            submission, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(FeedbackDetailSerializer(submission).data)


class FeedbackScreenshotView(APIView):
    """Serve the stored screenshot of a feedback submission."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, submission_id: UUID) -> HttpResponseBase:
        try:
            submission = FeedbackSubmission.objects.get(pk=submission_id)
        except FeedbackSubmission.DoesNotExist:
            raise NotFound() from None

        if submission.user_id != request.user.pk and not IsAdmin().has_permission(
            request, self
        ):
            raise PermissionDenied()

        key = submission.screenshot_object_key
        if not key:
            raise NotFound()

        response = image_response(FEEDBACK_IMAGE_PREFIX, submission.user_id, key)
        if response is None:
            raise NotFound()
        return response
