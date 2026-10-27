"""Photographic evidence uploaded with the action-log form."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from green_iteso.actions.evidence import (
    save_action_evidence,
    signed_evidence_url,
    signed_read_url,
)
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster

from .helpers import create_admin, create_student

LOGS_URL = "/api/v1/action-logs/"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def evidence_media(settings: Any, tmp_path: Path) -> Path:
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def _photo_action() -> ActionMaster:
    category = ActionCategory.objects.create(code="EVIDENCE", name="Evidence")
    return ActionMaster.objects.create(
        code="PHOTO_EVIDENCE",
        category=category,
        name="Photo evidence",
        description="Requires a photograph",
        points=5,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )


def _png(name: str = "evidence.png", payload: bytes = PNG_BYTES) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, payload, content_type="image/png")


@pytest.mark.django_db
def test_photo_registration_stores_the_form_image(evidence_media: Path) -> None:
    student = create_student()
    admin = create_admin()
    action = _photo_action()
    client = APIClient()
    client.force_authenticate(student)

    response = client.post(
        LOGS_URL,
        {
            "action_id": str(action.id),
            "idempotency_key": str(uuid.uuid4()),
            "evidence": _png(),
        },
        format="multipart",
    )

    assert response.status_code == 201
    log = ActionLog.objects.get(pk=response.json()["log_id"])
    assert log.status == ActionLog.Status.PENDING_AUDIT
    stored = evidence_media / log.evidence_object_key
    assert stored.is_file()
    assert stored.read_bytes() == PNG_BYTES

    client.force_authenticate(admin)
    queue = client.get(LOGS_URL)
    assert queue.status_code == 200
    row = queue.json()["results"][0]
    assert row["evidence_url"].endswith(f"/api/v1/action-logs/{log.id}/evidence/")

    image = client.get(row["evidence_url"])
    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"
    assert image.getvalue() == PNG_BYTES


@pytest.mark.django_db
def test_student_cannot_read_stored_evidence(evidence_media: Path) -> None:
    student = create_student()
    action = _photo_action()
    client = APIClient()
    client.force_authenticate(student)
    created = client.post(
        LOGS_URL,
        {
            "action_id": str(action.id),
            "idempotency_key": str(uuid.uuid4()),
            "evidence": _png(),
        },
        format="multipart",
    )
    log_id = created.json()["log_id"]

    assert client.get(f"{LOGS_URL}{log_id}/evidence/").status_code == 403
    assert APIClient().get(f"{LOGS_URL}{log_id}/evidence/").status_code == 401
    assert evidence_media.joinpath("action-evidence").is_dir()


@pytest.mark.django_db
def test_photo_registration_rejects_a_non_image(evidence_media: Path) -> None:
    student = create_student()
    action = _photo_action()
    client = APIClient()
    client.force_authenticate(student)

    response = client.post(
        LOGS_URL,
        {
            "action_id": str(action.id),
            "idempotency_key": str(uuid.uuid4()),
            "evidence": SimpleUploadedFile(
                "notes.txt", b"not an image", content_type="text/plain"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400
    assert response.json()["evidence"] == ["Usa una imagen JPG, PNG o WEBP."]
    assert ActionLog.objects.count() == 0
    assert not list(evidence_media.iterdir())


@pytest.mark.django_db
def test_photo_action_requires_evidence(evidence_media: Path) -> None:
    student = create_student()
    action = _photo_action()
    client = APIClient()
    client.force_authenticate(student)

    response = client.post(
        LOGS_URL,
        {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["evidence"] == [
        "Esta acción requiere evidencia fotográfica."
    ]
    assert not list(evidence_media.iterdir())


@pytest.mark.django_db
def test_admin_cannot_read_a_path_outside_evidence_storage(
    evidence_media: Path,
) -> None:
    student = create_student()
    action = _photo_action()
    secret = evidence_media.parent / "secret.txt"
    secret.write_text("hidden", encoding="utf-8")
    log = ActionLog.objects.create(
        user=student,
        action=action,
        institutional_clan=student.profile.institutional_clan,
        idempotency_key="outside",
        points_awarded=5,
        status=ActionLog.Status.PENDING_AUDIT,
        evidence_object_key="../secret.txt",
    )
    client = APIClient()
    client.force_authenticate(create_admin())

    response = client.get(f"{LOGS_URL}{log.id}/evidence/")

    assert response.status_code == 404
    assert secret.read_text(encoding="utf-8") == "hidden"


@pytest.mark.django_db
def test_repeating_the_same_image_does_not_store_a_second_copy(
    evidence_media: Path,
) -> None:
    student = create_student()
    action = _photo_action()
    client = APIClient()
    client.force_authenticate(student)
    payload = {
        "action_id": str(action.id),
        "idempotency_key": "same-photo",
        "evidence": _png(),
    }

    first = client.post(LOGS_URL, payload, format="multipart")
    payload["evidence"] = _png()
    replay = client.post(LOGS_URL, payload, format="multipart")

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["log_id"] == first.json()["log_id"]
    stored = list((evidence_media / "action-evidence").rglob("*.png"))
    assert len(stored) == 1


def test_local_development_ignores_a_cloud_bucket_name(
    settings: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    settings.DEPLOYED = False
    settings.MEDIA_ROOT = tmp_path
    settings.GCS_EVIDENCE_BUCKET = "greeniteso-production-objects"

    def forbid_cloud() -> None:
        raise AssertionError("cloud storage was contacted")

    monkeypatch.setattr("green_iteso.actions.evidence._gcs_bucket", forbid_cloud)
    key = save_action_evidence(uuid.uuid4(), _png())

    assert (tmp_path / key).is_file()
    assert (tmp_path / key).read_bytes() == PNG_BYTES


def test_deployed_service_stores_evidence_in_the_configured_bucket(
    settings: Any, monkeypatch: Any
) -> None:
    settings.DEPLOYED = True
    settings.GCS_EVIDENCE_BUCKET = "unit-test-objects"
    blobs: dict[str, Any] = {}

    class FakeBlob:
        def __init__(self, name: str) -> None:
            self.name = name
            self.payload = b""
            self.content_type = ""
            self.signed: dict[str, Any] | None = None

        def upload_from_string(self, payload: bytes, content_type: str = "") -> None:
            self.payload = payload
            self.content_type = content_type

        def exists(self) -> bool:
            return bool(self.payload)

        def download_as_bytes(self) -> bytes:
            return self.payload

        def delete(self) -> None:
            self.payload = b""

        def generate_signed_url(self, **kwargs: Any) -> str:
            self.signed = kwargs
            return "https://storage.example/signed-read"

    class FakeBucket:
        def blob(self, name: str) -> FakeBlob:
            blob = blobs.get(name)
            if blob is None:
                blob = FakeBlob(name)
                blobs[name] = blob
            return blob

    monkeypatch.setattr("green_iteso.actions.evidence._gcs_bucket", FakeBucket)
    monkeypatch.setattr(
        "green_iteso.actions.evidence._signing_credentials",
        lambda: ("backend@example.iam.gserviceaccount.com", "access-token"),
    )

    key = save_action_evidence(uuid.uuid4(), _png())

    assert key.startswith("action-evidence/")
    assert blobs[key].content_type == "image/png"
    assert blobs[key].payload == PNG_BYTES
    assert signed_evidence_url(key) == "https://storage.example/signed-read"
    assert blobs[key].signed is not None
    assert blobs[key].signed["method"] == "GET"
    assert blobs[key].signed["service_account_email"] == (
        "backend@example.iam.gserviceaccount.com"
    )
    assert blobs[key].signed["access_token"] == "access-token"
    assert signed_evidence_url("../secret.txt") == ""
    assert signed_evidence_url("demo-only/photo.jpg") == ""


def test_signed_read_url_returns_empty_without_signing_credentials(
    monkeypatch: Any,
) -> None:
    """Do not return a URL when runtime credentials cannot sign a read."""

    class ExistingBlob:
        def exists(self) -> bool:
            return True

        def generate_signed_url(self, **kwargs: Any) -> str:
            raise AssertionError("A URL must not be generated without credentials.")

    monkeypatch.setattr(
        "green_iteso.actions.evidence._signing_credentials", lambda: ("", "")
    )

    assert signed_read_url(ExistingBlob()) == ""
