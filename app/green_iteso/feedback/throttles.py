"""Rate limits for feedback endpoints."""

from __future__ import annotations

from rest_framework.throttling import UserRateThrottle


class FeedbackSubmitThrottle(UserRateThrottle):
    """Limit feedback submissions per user to prevent spam."""

    scope = "feedback_submit"
