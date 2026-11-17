"""Rate limits for feedback endpoints."""

from __future__ import annotations

from rest_framework.throttling import UserRateThrottle

from green_iteso.core.throttling import SharedCacheThrottleMixin


class FeedbackSubmitThrottle(SharedCacheThrottleMixin, UserRateThrottle):
    """Limit feedback submissions per user to prevent spam."""

    scope = "feedback_submit"
