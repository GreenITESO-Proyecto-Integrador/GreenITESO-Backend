"""Private storage for user-facing images such as clan and profile avatars.

The client uploads the file; it never chooses the object key. Keys are
always generated here as ``<prefix>/<owner_id>/<uuid>.<ext>``, so a stored
key is guaranteed to point at an image this app validated and wrote for that
owner. Storage follows ``actions.evidence``: local development keeps files
under MEDIA_ROOT, and a deployed service that sets ``GCS_EVIDENCE_BUCKET``
writes to that shared private bucket and serves short-lived signed URLs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, HttpResponseBase, HttpResponseRedirect
from google.cloud import storage

from green_iteso.actions.evidence import (
    detect_image_kind,
    gcs_evidence_enabled,
    signed_read_url,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_CONTENT_TYPES = {extension: kind for kind, extension in _EXTENSIONS.items()}


class InvalidImageError(ValueError):
    """Raised when an upload is not a JPG, PNG, or WEBP of at most 5 MB."""


def _gcs_bucket() -> Any:
    """Open the configured bucket; only called when ``gcs_evidence_enabled``."""
    return storage.Client().bucket(settings.GCS_EVIDENCE_BUCKET)


def validate_image(uploaded: UploadedFile) -> str:
    """Return the image media type, checked from the file signature."""
    if uploaded.size is None or uploaded.size > MAX_IMAGE_BYTES:
        raise InvalidImageError("The image must be at most 5 MB.")
    header = uploaded.read(16)
    uploaded.seek(0)
    kind = detect_image_kind(header)
    declared = getattr(uploaded, "content_type", "") or ""
    if kind is None or declared not in (kind, "application/octet-stream"):
        raise InvalidImageError("The image must be a JPG, PNG, or WEBP file.")
    return kind


def _read_bounded(uploaded: UploadedFile) -> bytes:
    """Read an upload, rejecting a body larger than the declared size allowed."""
    payload = bytearray()
    for chunk in uploaded.chunks():
        payload.extend(chunk)
        if len(payload) > MAX_IMAGE_BYTES:
            raise InvalidImageError("The image must be at most 5 MB.")
    return bytes(payload)


def is_owned_key(prefix: str, owner_id: UUID, object_key: str) -> bool:
    """Accept only keys this module generates for ``owner_id`` under ``prefix``."""
    if not object_key or ".." in Path(object_key).parts:
        return False
    return object_key.startswith(f"{prefix}/{owner_id}/")


def save_image(prefix: str, owner_id: UUID, uploaded: UploadedFile) -> str:
    """Validate and store ``uploaded``; return the new private object key."""
    kind = validate_image(uploaded)
    payload = _read_bounded(uploaded)
    object_key = f"{prefix}/{owner_id}/{uuid4()}{_EXTENSIONS[kind]}"
    if gcs_evidence_enabled():
        _gcs_bucket().blob(object_key).upload_from_string(payload, content_type=kind)
    else:
        destination = Path(settings.MEDIA_ROOT) / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return object_key


def delete_image(prefix: str, owner_id: UUID, object_key: str) -> None:
    """Best-effort removal of a stored image; other keys are left untouched.

    Runs after the database change commits, so a storage failure is logged
    rather than turning an already-applied change into a 500.
    """
    if not is_owned_key(prefix, owner_id, object_key):
        return
    try:
        if gcs_evidence_enabled():
            blob = _gcs_bucket().blob(object_key)
            if blob.exists():
                blob.delete()
            return
        (Path(settings.MEDIA_ROOT) / object_key).unlink(missing_ok=True)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not delete a stored image.")


def image_response(
    prefix: str, owner_id: UUID, object_key: str
) -> HttpResponseBase | None:
    """Serve a stored image, or return None when there is nothing to serve.

    A deployed service redirects to a signed URL of the private bucket;
    local development streams the file from MEDIA_ROOT.
    """
    if not is_owned_key(prefix, owner_id, object_key):
        return None
    if gcs_evidence_enabled():
        signed_url = signed_read_url(_gcs_bucket().blob(object_key))
        return HttpResponseRedirect(signed_url) if signed_url else None
    path = Path(settings.MEDIA_ROOT) / object_key
    if not path.is_file():
        return None
    content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path.open("rb"), content_type=content_type)
