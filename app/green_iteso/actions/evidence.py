"""Private storage for photographic action evidence.

The registration form sends the image as the multipart field ``evidence``.
Local development keeps the file under MEDIA_ROOT. A deployed service that
sets ``GCS_EVIDENCE_BUCKET`` stores the same object key in that private bucket
and serves administrators a short-lived signed URL. The bucket name is never
hardcoded, and this module does not contact cloud storage unless that
deployed setting is on.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage
from rest_framework.exceptions import ValidationError

EVIDENCE_PREFIX = "action-evidence"
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024

_JPEG = "image/jpeg"
_PNG = "image/png"
_WEBP = "image/webp"
_EXTENSIONS = {_JPEG: ".jpg", _PNG: ".png", _WEBP: ".webp"}
SIGNED_URL_TTL = timedelta(minutes=10)


def gcs_evidence_enabled() -> bool:
    """Use the private bucket only on a deployed service that names one.

    Local development keeps ``DJANGO_DEPLOYED=false``. A bucket name in the
    environment is ignored in that case, so development cannot write to a
    cloud bucket by accident.
    """
    bucket = str(getattr(settings, "GCS_EVIDENCE_BUCKET", "")).strip()
    return bool(bucket) and bool(getattr(settings, "DEPLOYED", False))


def safe_object_key(object_key: str) -> str | None:
    """Accept only keys this app generated under the evidence prefix."""
    if not object_key or object_key.startswith(("/", "\\")):
        return None
    if ".." in Path(object_key).parts:
        return None
    if not object_key.startswith(f"{EVIDENCE_PREFIX}/"):
        return None
    return object_key


def _gcs_bucket() -> Any:
    """Open the configured bucket with the process credentials.

    Cloud Run uses the runtime service account. This runs only after
    ``gcs_evidence_enabled`` is true.
    """
    return storage.Client().bucket(settings.GCS_EVIDENCE_BUCKET)


def _signing_credentials() -> tuple[str, str]:
    """Return the service-account email and access token used to sign a read."""
    credentials, _project = default()
    credentials.refresh(GoogleAuthRequest())
    email = str(getattr(credentials, "service_account_email", "") or "")
    token = str(getattr(credentials, "token", "") or "")
    return email, token


def detect_image_kind(header: bytes) -> str | None:
    """Return the image media type implied by the file signature."""
    if header.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return _WEBP
    return None


def validate_evidence_upload(uploaded: UploadedFile) -> str:
    """Reject uploads that are not a JPG, PNG, or WEBP of at most 5 MB."""
    if uploaded.size > MAX_EVIDENCE_BYTES:
        raise ValidationError("La evidencia no puede superar 5 MB.")
    header = uploaded.read(16)
    uploaded.seek(0)
    kind = detect_image_kind(header)
    if kind is None:
        raise ValidationError("Usa una imagen JPG, PNG o WEBP.")
    declared = getattr(uploaded, "content_type", "") or ""
    if declared in _EXTENSIONS and declared != kind:
        raise ValidationError("Usa una imagen JPG, PNG o WEBP.")
    if (
        declared
        and declared not in _EXTENSIONS
        and declared != "application/octet-stream"
    ):
        raise ValidationError("Usa una imagen JPG, PNG o WEBP.")
    return kind


def _read_bounded(uploaded: UploadedFile) -> bytes:
    """Read an upload, rejecting a body larger than the evidence limit."""
    written = 0
    payload = bytearray()
    for chunk in uploaded.chunks():
        written += len(chunk)
        if written > MAX_EVIDENCE_BYTES:
            raise ValidationError("La evidencia no puede superar 5 MB.")
        payload.extend(chunk)
    return bytes(payload)


def _upload_gcs(object_key: str, payload: bytes, kind: str) -> None:
    """Store bytes in the configured bucket. A failed upload removes the object."""
    blob = _gcs_bucket().blob(object_key)
    try:
        blob.upload_from_string(payload, content_type=kind)
    except Exception:
        try:
            blob.delete()
        except Exception:
            pass
        raise


def save_action_evidence(user_id: UUID, uploaded: UploadedFile) -> str:
    """Store ``uploaded`` and return the private object key."""
    kind = validate_evidence_upload(uploaded)
    object_key = (
        Path(EVIDENCE_PREFIX) / str(user_id) / f"{uuid4()}{_EXTENSIONS[kind]}"
    ).as_posix()
    payload = _read_bounded(uploaded)
    if gcs_evidence_enabled():
        _upload_gcs(object_key, payload, kind)
    else:
        destination = Path(settings.MEDIA_ROOT) / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(payload)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    uploaded.seek(0)
    return object_key


def delete_action_evidence(object_key: str) -> None:
    """Remove a file this module stored. Other keys are left untouched."""
    if gcs_evidence_enabled():
        key = safe_object_key(object_key)
        if key is None:
            return
        blob = _gcs_bucket().blob(key)
        if blob.exists():
            blob.delete()
        return
    path = evidence_file_path(object_key)
    if path is not None:
        path.unlink(missing_ok=True)


def evidence_file_path(object_key: str) -> Path | None:
    """Return the stored file when ``object_key`` points at one we wrote."""
    if not object_key or ".." in Path(object_key).parts:
        return None
    root = (Path(settings.MEDIA_ROOT) / EVIDENCE_PREFIX).resolve()
    candidate = (Path(settings.MEDIA_ROOT) / object_key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def evidence_is_readable(object_key: str) -> bool:
    """Report whether an administrator can open this evidence.

    The cloud check stays on the read request. Listing the queue does not
    contact the bucket; it only accepts keys shaped like ones this app stores.
    """
    if gcs_evidence_enabled():
        return safe_object_key(object_key) is not None
    return evidence_file_path(object_key) is not None


def signed_evidence_url(object_key: str) -> str:
    """Return a short-lived read URL for one object in the configured bucket."""
    key = safe_object_key(object_key)
    if key is None or not gcs_evidence_enabled():
        return ""
    blob = _gcs_bucket().blob(key)
    if not blob.exists():
        return ""
    try:
        email, token = _signing_credentials()
    except Exception:
        return ""
    if not email or not token:
        return ""
    return str(
        blob.generate_signed_url(
            version="v4",
            expiration=SIGNED_URL_TTL,
            method="GET",
            service_account_email=email,
            access_token=token,
        )
    )


def evidence_content_type(path: Path) -> str:
    """Return the media type for a stored evidence file."""
    return {
        ".jpg": _JPEG,
        ".jpeg": _JPEG,
        ".png": _PNG,
        ".webp": _WEBP,
    }.get(path.suffix.lower(), "application/octet-stream")


def evidence_fingerprint(uploaded: UploadedFile) -> str:
    """Hash an upload so a repeated submission can be compared with the stored file."""
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


def stored_evidence_fingerprint(object_key: str) -> str:
    """Hash a stored evidence file. Missing files hash as empty."""
    if gcs_evidence_enabled():
        key = safe_object_key(object_key)
        if key is None:
            return ""
        blob = _gcs_bucket().blob(key)
        if not blob.exists():
            return ""
        payload = blob.download_as_bytes()
        if len(payload) > MAX_EVIDENCE_BYTES:
            return ""
        return hashlib.sha256(payload).hexdigest()
    path = evidence_file_path(object_key)
    if path is None:
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
