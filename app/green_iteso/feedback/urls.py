"""Routes for feedback."""

from django.urls import path

from .views import (
    FeedbackDetailView,
    FeedbackListCreateView,
    FeedbackScreenshotView,
    MyFeedbackListView,
)

urlpatterns = [
    path("feedback/", FeedbackListCreateView.as_view(), name="feedback-list-create"),
    path("feedback/mine/", MyFeedbackListView.as_view(), name="feedback-mine"),
    path(
        "feedback/<uuid:submission_id>/",
        FeedbackDetailView.as_view(),
        name="feedback-detail",
    ),
    path(
        "feedback/<uuid:submission_id>/screenshot/",
        FeedbackScreenshotView.as_view(),
        name="feedback-screenshot",
    ),
]
