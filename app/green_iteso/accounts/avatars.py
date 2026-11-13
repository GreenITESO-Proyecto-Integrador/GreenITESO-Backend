"""Profile photos uploaded from the device (#170).

The file is stored privately by ``core.image_storage`` under
``avatars/<user_id>/<uuid>.<ext>``. Clients never see the object key: they
get ``avatar_url``, which points at ``GET /api/v1/avatars/<user_id>/``. That
URL is stable, so it works in an ``<img>`` anywhere a user is shown, and the
view behind it redirects to a short-lived signed URL of the bucket.

Private profiles (#238): the photo follows the real name. A ``PRIVATE``
profile hides its real name from other people, so its photo (uploaded or a
pasted link) is only given to the person themselves and to admins; everyone
else gets "". Since an ``<img>`` cannot send the Bearer token, the serving
endpoint cannot tell who is asking, so the URL handed out for a private
upload carries ``sig``, an HMAC of the user id and the photo version. Without
a valid ``sig`` the endpoint answers 404 for a private profile, so the photo
is not reachable by guessing ``/avatars/<user_id>/``. A new upload changes
the version and so invalidates earlier URLs.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlencode
from uuid import UUID

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from rest_framework.request import Request

from green_iteso.core.image_storage import delete_image, save_image

from .models import User, UserProfile

PROFILE_AVATAR_PREFIX = "avatars"
_SIGNATURE_SALT = "accounts.private-profile-avatar"


def save_profile_avatar(user_id: UUID, uploaded: UploadedFile) -> str:
    """Validate and store ``uploaded``; return the new private object key."""
    return save_image(PROFILE_AVATAR_PREFIX, user_id, uploaded)


def delete_profile_avatar(user_id: UUID, object_key: str) -> None:
    """Best-effort removal of a stored profile photo; other keys are left untouched."""
    delete_image(PROFILE_AVATAR_PREFIX, user_id, object_key)


def avatar_version(object_key: str) -> str:
    """The ``v`` of a photo's URL: the stored file's name, new on every upload."""
    return PurePosixPath(object_key).stem


def private_avatar_signature(user_id: UUID | str, version: str) -> str:
    """The ``sig`` that unlocks a private profile's photo at this version."""
    return signing.Signer(salt=_SIGNATURE_SALT).signature(f"{user_id}:{version}")


def is_valid_private_avatar_signature(
    user_id: UUID | str, version: str, signature: str
) -> bool:
    """Whether ``signature`` was issued for this user's photo at this version."""
    expected = private_avatar_signature(user_id, version)
    return bool(signature) and constant_time_compare(signature, expected)


def _may_see_private_photo(profile: UserProfile, request: Request | None) -> bool:
    viewer = getattr(request, "user", None)
    if viewer is None or not viewer.is_authenticated:
        return False
    return viewer.pk == profile.user_id or viewer.role == User.Role.ADMIN


def public_avatar_url(profile: UserProfile | None, request: Request | None) -> str:
    """Return the URL ``request``'s user should load for this profile's photo, or "".

    An uploaded photo resolves to the serving endpoint, versioned by the
    object's file name so a new upload is never hidden by a cached image. A
    pasted link is returned as is. A ``PRIVATE`` profile's photo is "" for
    anyone but the person and admins, and its upload URL is signed (#238).
    """
    if profile is None:
        return ""
    is_private = profile.visibility == UserProfile.Visibility.PRIVATE
    if is_private and not _may_see_private_photo(profile, request):
        return ""
    if profile.avatar_object_key:
        path = reverse("profile-avatar", kwargs={"user_id": profile.user_id})
        version = avatar_version(profile.avatar_object_key)
        params = {"v": version}
        if is_private:
            params["sig"] = private_avatar_signature(profile.user_id, version)
        url = f"{path}?{urlencode(params)}"
        return request.build_absolute_uri(url) if request is not None else url
    return profile.avatar_url


def user_avatar_url(user: User, request: Request | None) -> str:
    """``public_avatar_url`` for a user whose profile may not exist yet."""
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        return ""
    return public_avatar_url(profile, request)
