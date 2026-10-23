"""Shared fixtures for tests of ``core.image_storage`` uploads (clan and profile avatars)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def use_local_media(settings: Any, tmp_path: Path) -> Path:
    """Store uploads under ``tmp_path`` the way local development does."""
    settings.DEPLOYED = False
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def png_upload(payload: bytes = PNG_BYTES, content_type: str = "image/png") -> Any:
    """A multipart file field holding ``payload``."""
    return SimpleUploadedFile("image.png", payload, content_type=content_type)


def stored_files(media: Path) -> list[Path]:
    """Every file currently stored under ``media``."""
    return [path for path in media.rglob("*") if path.is_file()]


def write_outsider_file(media: Path) -> Path:
    """A file outside any avatar prefix, which deletions must never touch."""
    outsider = media / "action-evidence" / "keep.png"
    outsider.parent.mkdir(parents=True)
    outsider.write_bytes(PNG_BYTES)
    return outsider


def fake_gcs_bucket(uploads: dict[str, tuple[bytes, str]]) -> type:
    """A stand-in for the private bucket that records uploads and signs reads."""

    class FakeBlob:
        def __init__(self, name: str) -> None:
            self.name = name

        def upload_from_string(self, payload: bytes, content_type: str) -> None:
            uploads[self.name] = (payload, content_type)

        def exists(self) -> bool:
            return self.name in uploads

        def generate_signed_url(self, **kwargs: Any) -> str:
            assert kwargs["method"] == "GET"
            return f"https://storage.example/{self.name}?signed"

    class FakeBucket:
        def blob(self, name: str) -> FakeBlob:
            return FakeBlob(name)

    return FakeBucket


def use_fake_gcs(settings: Any, monkeypatch: Any) -> dict[str, tuple[bytes, str]]:
    """Switch to the deployed storage path backed by ``fake_gcs_bucket``."""
    settings.DEPLOYED = True
    settings.GCS_EVIDENCE_BUCKET = "greeniteso-test-objects"
    uploads: dict[str, tuple[bytes, str]] = {}
    monkeypatch.setattr(
        "green_iteso.core.image_storage._gcs_bucket", fake_gcs_bucket(uploads)
    )
    monkeypatch.setattr(
        "green_iteso.actions.evidence._signing_credentials",
        lambda: ("runtime@example.iam.gserviceaccount.com", "token"),
    )
    return uploads
