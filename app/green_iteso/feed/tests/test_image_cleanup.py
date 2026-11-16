# pylint: disable=redefined-outer-name
"""Feed images are removed from storage with the last post that uses them."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from django.db import connection, transaction
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.accounts.services import deactivate_account
from green_iteso.core.tests.image_helpers import (
    png_upload,
    stored_files,
    use_local_media,
)
from green_iteso.feed.images import schedule_feed_image_cleanup
from green_iteso.feed.models import Post, PostType

HOST = "api.greeniteso.test"


@pytest.fixture(autouse=True)
def allowed_host(settings: Any) -> None:
    settings.ALLOWED_HOSTS = [HOST]


@pytest.fixture
def media(settings: Any, tmp_path: Path) -> Path:
    return use_local_media(settings, tmp_path)


@pytest.fixture
def author() -> User:
    return User.objects.create_user(email="autor@iteso.mx", password="local-only")


@pytest.fixture
def other() -> User:
    return User.objects.create_user(email="otro@iteso.mx", password="local-only")


def _client(user: User) -> APIClient:
    client = APIClient(HTTP_HOST=HOST)
    client.force_authenticate(user)
    return client


def _upload(user: User) -> str:
    response = _client(user).post(
        "/api/v1/uploads/", {"image": png_upload()}, format="multipart"
    )
    assert response.status_code == 200
    return response.data["url"]


def _post(user: User, image_url: str = "") -> Post:
    return Post.objects.create(
        author=user,
        post_type=PostType.SHARED_EVIDENCE,
        content="Publicación",
        image_url=image_url,
    )


@pytest.mark.django_db
def test_deleting_a_post_removes_its_image(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    post = _post(author, _upload(author))
    assert len(stored_files(media)) == 1

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(author).delete(f"/api/v1/feed/{post.pk}/")

    assert response.status_code == 204
    assert stored_files(media) == []


@pytest.mark.django_db
def test_an_image_still_used_by_another_post_is_kept(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    url = _upload(author)
    first, _second = _post(author, url), _post(author, url)

    with django_capture_on_commit_callbacks(execute=True):
        _client(author).delete(f"/api/v1/feed/{first.pk}/")

    assert len(stored_files(media)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "variant",
    [
        lambda url: url.replace("http://", "https://"),
        lambda url: f"{url}?v=2",
    ],
    ids=["other-scheme", "query-string"],
)
def test_an_image_used_through_another_url_form_is_kept(
    media: Path,
    author: User,
    other: User,
    django_capture_on_commit_callbacks: Any,
    variant: Any,
) -> None:
    url = _upload(author)
    first = _post(author, url)
    _post(other, variant(url))

    with django_capture_on_commit_callbacks(execute=True):
        _client(author).delete(f"/api/v1/feed/{first.pk}/")

    assert len(stored_files(media)) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_deletes_of_posts_sharing_an_image_leave_no_orphan(
    media: Path, author: User
) -> None:
    url = _upload(author)
    post_ids = [_post(author, url).pk, _post(author, url).pk]
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def delete_post(post_id: int) -> None:
        try:
            with transaction.atomic():
                Post.objects.get(pk=post_id).delete()
                schedule_feed_image_cleanup([url], author.pk)
                # Both deletes are uncommitted here: each one still sees the
                # other post, so only the check after commit can be right.
                barrier.wait(timeout=10)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=delete_post, args=(pk,)) for pk in post_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert not Post.objects.filter(author=author).exists()
    assert stored_files(media) == []


@pytest.mark.django_db
def test_a_url_to_someone_elses_image_is_not_deleted(
    media: Path, author: User, other: User, django_capture_on_commit_callbacks: Any
) -> None:
    foreign_url = _upload(other)
    post = _post(author, foreign_url)

    with django_capture_on_commit_callbacks(execute=True):
        _client(author).delete(f"/api/v1/feed/{post.pk}/")

    assert len(stored_files(media)) == 1


@pytest.mark.django_db
def test_an_admin_deleting_a_post_removes_the_authors_image(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    post = _post(author, _upload(author))

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(admin).delete(f"/api/v1/feed/{post.pk}/")

    assert response.status_code == 204
    assert stored_files(media) == []


@pytest.mark.django_db
def test_an_external_url_is_ignored(
    author: User, django_capture_on_commit_callbacks: Any
) -> None:
    post = _post(author, "https://example.com/cat.png")

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(author).delete(f"/api/v1/feed/{post.pk}/")

    assert response.status_code == 204


@pytest.mark.django_db
def test_replacing_the_image_removes_the_old_one(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    old_url = _upload(author)
    post = _post(author, old_url)
    new_url = _upload(author)
    assert len(stored_files(media)) == 2

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(author).patch(
            f"/api/v1/feed/{post.pk}/", {"image_url": new_url}, format="json"
        )

    assert response.status_code == 200
    remaining = stored_files(media)
    assert len(remaining) == 1
    assert remaining[0].name == new_url.rsplit("/", 1)[1]


@pytest.mark.django_db
def test_editing_only_the_text_keeps_the_image(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    post = _post(author, _upload(author))

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(author).patch(
            f"/api/v1/feed/{post.pk}/", {"content": "Editado"}, format="json"
        )

    assert response.status_code == 200
    assert len(stored_files(media)) == 1


@pytest.mark.django_db
def test_deactivating_an_account_removes_its_post_images(
    media: Path, author: User, django_capture_on_commit_callbacks: Any
) -> None:
    _post(author, _upload(author))
    _post(author, _upload(author))
    _post(author)
    assert len(stored_files(media)) == 2

    with django_capture_on_commit_callbacks(execute=True):
        deactivate_account(user=author)

    assert not Post.objects.filter(author=author).exists()
    assert stored_files(media) == []
