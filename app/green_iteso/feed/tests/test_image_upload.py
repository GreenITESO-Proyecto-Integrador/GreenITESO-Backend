# pylint: disable=redefined-outer-name
"""Feed image upload: ``POST /api/v1/uploads/`` and the URL it returns.

The upload returns a stable app URL, not a signed bucket URL, so it fits in
``Post.image_url`` (500) and never expires. Reading it streams the file
locally and redirects to a short-lived signed URL when deployed.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from google.cloud.exceptions import GoogleCloudError
from PIL import Image
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from green_iteso.accounts.models import User
from green_iteso.core.image_storage import MAX_IMAGE_BYTES
from green_iteso.core.tests.image_helpers import (
    PNG_BYTES,
    png_upload,
    stored_files,
    use_fake_gcs,
    use_local_media,
)
from green_iteso.feed.models import Post, PostType

UPLOAD_URL = "/api/v1/uploads/"
HOST = "api.greeniteso.test"


@pytest.fixture(autouse=True)
def allowed_host(settings: Any) -> None:
    """Serve on a host that ``URLField`` accepts; "testserver" has no TLD."""
    settings.ALLOWED_HOSTS = [HOST]


@pytest.fixture
def feed_media(settings: Any, tmp_path: Path) -> Path:
    """Store uploads under ``tmp_path`` the way local development does."""
    return use_local_media(settings, tmp_path)


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="autor@iteso.mx", password="local-only")


def _client(user: User | None = None) -> APIClient:
    client = APIClient(HTTP_HOST=HOST)
    if user is not None:
        client.force_authenticate(user)
    return client


def _upload(user: User, upload: Any) -> Any:
    return _client(user).post(UPLOAD_URL, {"image": upload}, format="multipart")


def _phone_jpeg() -> bytes:
    """A multi-picture JPEG, which Pillow reports as MPO, like many phone photos."""
    buffer = io.BytesIO()
    first, second = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
    first.save(buffer, "MPO", save_all=True, append_images=[second])
    return buffer.getvalue()


def test_upload_requires_authentication() -> None:
    response = _client().post(UPLOAD_URL, {}, format="multipart")

    assert response.status_code == 401


@pytest.mark.django_db
def test_upload_without_a_file_is_rejected(user: User) -> None:
    response = _client(user).post(UPLOAD_URL, {}, format="multipart")

    assert response.status_code == 400
    assert "error" in response.data


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        pytest.param(b"contenido de texto plano", "text/plain", id="text"),
        pytest.param(b"GIF89a" + b"\x00" * 32, "image/gif", id="gif"),
        pytest.param(PNG_BYTES, "image/jpeg", id="declared-type-mismatch"),
        pytest.param(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png", id="truncated"
        ),
        # These open fine and only fail Pillow's verify().
        pytest.param(PNG_BYTES[:45], "image/png", id="cut-body"),
        pytest.param(
            PNG_BYTES[:-17] + b"\x00" * 4 + PNG_BYTES[-12:],
            "image/png",
            id="bad-checksum",
        ),
        pytest.param(
            b"\xff\xd8\xff" + b"0" * MAX_IMAGE_BYTES, "image/jpeg", id="over-5mb"
        ),
    ],
)
def test_invalid_images_are_rejected_and_not_stored(
    feed_media: Path, user: User, payload: bytes, content_type: str
) -> None:
    response = _upload(user, png_upload(payload, content_type))

    assert response.status_code == 400
    assert "error" in response.data
    assert stored_files(feed_media) == []


@pytest.mark.django_db
def test_local_upload_returns_a_stable_url_that_serves_the_image(
    feed_media: Path, user: User
) -> None:
    response = _upload(user, png_upload())

    assert response.status_code == 200
    url = response.data["url"]
    parts = urlsplit(url)
    assert parts.netloc == HOST
    assert parts.path.startswith(f"/api/v1/uploads/{user.pk}/")
    assert parts.path.endswith(".png")
    assert len(url) < Post._meta.get_field("image_url").max_length
    assert [p.relative_to(feed_media).parts[:2] for p in stored_files(feed_media)] == [
        ("feed", str(user.pk))
    ]

    image = _client().get(parts.path)

    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"
    assert b"".join(image.streaming_content) == PNG_BYTES


@pytest.mark.django_db
def test_a_phone_jpeg_is_accepted(feed_media: Path, user: User) -> None:
    response = _upload(user, png_upload(_phone_jpeg(), "image/jpeg"))

    assert response.status_code == 200
    assert urlsplit(response.data["url"]).path.endswith(".jpg")
    assert len(stored_files(feed_media)) == 1


@pytest.mark.django_db
def test_the_returned_url_can_be_saved_on_a_post(feed_media: Path, user: User) -> None:
    del feed_media
    url = _upload(user, png_upload()).data["url"]

    response = _client(user).post(
        "/api/v1/feed/",
        {
            "content": "Mi composta",
            "post_type": PostType.COMMUNITY_MILESTONE,
            "image_url": url,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Post.objects.get(pk=response.data["id"]).image_url == url


@pytest.mark.django_db
def test_deployed_upload_stores_in_the_bucket_and_redirects_to_a_signed_url(
    settings: Any, monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    uploads = use_fake_gcs(settings, monkeypatch)

    response = _upload(user, png_upload())

    assert response.status_code == 200
    url = response.data["url"]
    assert "storage" not in url and "Signature" not in url
    assert len(uploads) == 1
    object_key = next(iter(uploads))
    assert object_key.startswith(f"feed/{user.pk}/")
    assert uploads[object_key] == (PNG_BYTES, "image/png")

    image = _client().get(urlsplit(url).path)

    assert image.status_code == 302
    assert image["Location"] == f"https://storage.example/{object_key}?signed"
    assert image["Cache-Control"] == "private, max-age=300"


@pytest.mark.django_db
def test_a_storage_failure_returns_500(
    settings: Any, monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    use_fake_gcs(settings, monkeypatch)

    class FailingBucket:
        def blob(self, _name: str) -> Any:
            raise GoogleCloudError("bucket unavailable")

    monkeypatch.setattr("green_iteso.core.image_storage._gcs_bucket", FailingBucket)

    response = _upload(user, png_upload())

    assert response.status_code == 500
    assert "error" in response.data


@pytest.mark.django_db
def test_unknown_images_are_not_found(feed_media: Path, user: User) -> None:
    del feed_media
    path = urlsplit(_upload(user, png_upload()).data["url"]).path
    other_owner = "00000000-0000-4000-8000-000000000000"

    assert _client().get(path.replace(str(user.pk), other_owner)).status_code == 404
    assert _client().get(path.replace(".png", ".jpg")).status_code == 404
    assert _client().get(f"/api/v1/uploads/{user.pk}/..").status_code == 404


@pytest.mark.django_db
def test_uploads_are_throttled(
    feed_media: Path, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    del feed_media
    # DRF reads DEFAULT_THROTTLE_RATES into a class attribute at import time.
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {"feed_image_upload": "2/min"}
    )

    statuses = [_upload(user, png_upload()).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
