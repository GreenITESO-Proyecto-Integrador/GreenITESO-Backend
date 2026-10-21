"""Private storage for clan avatars (#164).

The leader uploads the image as the multipart field ``avatar``; the client
never chooses the object key. Keys are always generated here as
``clan-avatars/<clan_id>/<uuid>.<ext>``, so a stored key is guaranteed to
point at an image this app validated and wrote for that clan. Storage
follows ``actions.evidence``: local development keeps files under
MEDIA_ROOT, and a deployed service that sets ``GCS_EVIDENCE_BUCKET`` writes
to that shared private bucket instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from google.cloud import storage

from green_iteso.actions.evidence import detect_image_kind, gcs_evidence_enabled

logger = logging.getLogger(__name__)

CLAN_AVATAR_PREFIX = "clan-avatars"
MAX_CLAN_AVATAR_BYTES = 5 * 1024 * 1024

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class InvalidClanAvatarError(ValueError):
    """Raised when an upload is not a JPG, PNG, or WEBP of at most 5 MB."""


def _gcs_bucket() -> Any:
    """Open the configured bucket; only called when ``gcs_evidence_enabled``."""
    return storage.Client().bucket(settings.GCS_EVIDENCE_BUCKET)


def validate_clan_avatar(uploaded: UploadedFile) -> str:
    """Return the image media type, checked from the file signature."""
    if uploaded.size is None or uploaded.size > MAX_CLAN_AVATAR_BYTES:
        raise InvalidClanAvatarError("The avatar must be at most 5 MB.")
    header = uploaded.read(16)
    uploaded.seek(0)
    kind = detect_image_kind(header)
    declared = getattr(uploaded, "content_type", "") or ""
    if kind is None or declared not in (kind, "application/octet-stream"):
        raise InvalidClanAvatarError("The avatar must be a JPG, PNG, or WEBP image.")
    return kind


def _read_bounded(uploaded: UploadedFile) -> bytes:
    """Read an upload, rejecting a body larger than the declared size allowed."""
    payload = bytearray()
    for chunk in uploaded.chunks():
        payload.extend(chunk)
        if len(payload) > MAX_CLAN_AVATAR_BYTES:
            raise InvalidClanAvatarError("The avatar must be at most 5 MB.")
    return bytes(payload)


def is_clan_avatar_key(clan_id: UUID, object_key: str) -> bool:
    """Accept only keys this module generates for ``clan_id``."""
    if not object_key or ".." in Path(object_key).parts:
        return False
    return object_key.startswith(f"{CLAN_AVATAR_PREFIX}/{clan_id}/")


def save_clan_avatar(clan_id: UUID, uploaded: UploadedFile) -> str:
    """Validate and store ``uploaded``; return the new private object key."""
    kind = validate_clan_avatar(uploaded)
    payload = _read_bounded(uploaded)
    object_key = f"{CLAN_AVATAR_PREFIX}/{clan_id}/{uuid4()}{_EXTENSIONS[kind]}"
    if gcs_evidence_enabled():
        _gcs_bucket().blob(object_key).upload_from_string(payload, content_type=kind)
    else:
        destination = Path(settings.MEDIA_ROOT) / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return object_key


def delete_clan_avatar(clan_id: UUID, object_key: str) -> None:
    """Best-effort removal of a stored avatar; other keys are left untouched.

    Runs after the database change commits, so a storage failure is logged
    rather than turning an already-applied change into a 500.
    """
    if not is_clan_avatar_key(clan_id, object_key):
        return
    try:
        if gcs_evidence_enabled():
            blob = _gcs_bucket().blob(object_key)
            if blob.exists():
                blob.delete()
            return
        (Path(settings.MEDIA_ROOT) / object_key).unlink(missing_ok=True)
    except Exception:
        logger.exception("Could not delete a replaced clan avatar.")
