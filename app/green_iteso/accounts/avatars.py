"""Profile photos uploaded from the device (#170).

The file is stored privately by ``core.image_storage`` under
``avatars/<user_id>/<uuid>.<ext>``. Clients never see the object key: they
get ``avatar_url``, which points at ``GET /api/v1/avatars/<user_id>/``. That
URL is stable, so it works in an ``<img>`` anywhere a user is shown, and the
view behind it redirects to a short-lived signed URL of the bucket.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from rest_framework.request import Request

from green_iteso.core.image_storage import delete_image, save_image

from .models import User, UserProfile

PROFILE_AVATAR_PREFIX = "avatars"


def save_profile_avatar(user_id: UUID, uploaded: UploadedFile) -> str:
    """Validate and store ``uploaded``; return the new private object key."""
    return save_image(PROFILE_AVATAR_PREFIX, user_id, uploaded)


def delete_profile_avatar(user_id: UUID, object_key: str) -> None:
    """Best-effort removal of a stored profile photo; other keys are left untouched."""
    delete_image(PROFILE_AVATAR_PREFIX, user_id, object_key)


def public_avatar_url(profile: UserProfile | None, request: Request | None) -> str:
    """Return the URL a client should load for this profile's photo, or "".

    An uploaded photo resolves to the serving endpoint, versioned by the
    object's file name so a new upload is never hidden by a cached image. A
    pasted link is returned as is.
    """
    if profile is None:
        return ""
    if profile.avatar_object_key:
        path = reverse("profile-avatar", kwargs={"user_id": profile.user_id})
        version = PurePosixPath(profile.avatar_object_key).stem
        url = f"{path}?v={version}"
        return request.build_absolute_uri(url) if request is not None else url
    return profile.avatar_url


def user_avatar_url(user: User, request: Request | None) -> str:
    """``public_avatar_url`` for a user whose profile may not exist yet."""
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        return ""
    return public_avatar_url(profile, request)
