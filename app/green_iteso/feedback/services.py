"""Business logic for feedback submissions."""

from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from green_iteso.accounts.models import User
from green_iteso.core.image_storage import delete_image, save_image

from .models import FeedbackSubmission

FEEDBACK_IMAGE_PREFIX = "feedback"


def create_feedback_submission(
    *,
    user: User,
    category: str,
    subject: str,
    message: str,
    rating: int | None = None,
    screenshot: UploadedFile | None = None,
) -> FeedbackSubmission:
    """Create a feedback submission with optional screenshot."""
    screenshot_key = ""
    if screenshot is not None:
        screenshot_key = save_image(FEEDBACK_IMAGE_PREFIX, user.pk, screenshot)

    try:
        submission = FeedbackSubmission.objects.create(
            user=user,
            category=category,
            rating=rating,
            subject=subject.strip(),
            message=message.strip(),
            screenshot_object_key=screenshot_key,
        )
    except Exception:
        if screenshot_key:
            delete_image(FEEDBACK_IMAGE_PREFIX, user.pk, screenshot_key)
        raise

    return submission


def delete_feedback_submission(submission: FeedbackSubmission) -> None:
    """Delete a feedback submission and clean up its stored screenshot."""
    key = submission.screenshot_object_key
    user_id = submission.user_id
    submission.delete()
    if key:
        transaction.on_commit(lambda: delete_image(FEEDBACK_IMAGE_PREFIX, user_id, key))
