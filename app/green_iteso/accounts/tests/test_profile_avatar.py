"""Coverage for profile photo uploads and the avatar URL everywhere (#170)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from green_iteso.accounts.avatars import PROFILE_AVATAR_PREFIX, delete_profile_avatar
from green_iteso.accounts.models import ClanMembership, User, UserProfile
from green_iteso.clans.services import create_private_clan
from green_iteso.core.image_storage import MAX_IMAGE_BYTES
from green_iteso.core.tests.image_helpers import (
    PNG_BYTES,
    stored_files,
    use_fake_gcs,
    use_local_media,
    write_outsider_file,
)
from green_iteso.core.tests.image_helpers import png_upload as _png
from green_iteso.feed.models import Post, PostType

PROFILE_URL = "/api/v1/profile/me/"
AVATAR_URL = "/api/v1/profile/me/avatar/"


@pytest.fixture
def avatar_media(settings: Any, tmp_path: Path) -> Path:
    return use_local_media(settings, tmp_path)


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="ana@iteso.mx", first_name="Ana")


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _key(user: User) -> str:
    return UserProfile.objects.get(user=user).avatar_object_key


@pytest.mark.django_db
def test_upload_requires_authentication() -> None:
    response = APIClient().post(AVATAR_URL, {"avatar": _png()})

    assert response.status_code == 401


@pytest.mark.django_db
def test_upload_stores_the_photo_under_a_user_scoped_key(
    user: User, avatar_media: Path
) -> None:
    response = _client(user).post(AVATAR_URL, {"avatar": _png()})

    assert response.status_code == 200
    key = _key(user)
    assert key.startswith(f"{PROFILE_AVATAR_PREFIX}/{user.pk}/")
    assert key.endswith(".png")
    assert (avatar_media / key).read_bytes() == PNG_BYTES
    avatar_url = response.json()["avatar_url"]
    assert avatar_url.startswith(f"http://testserver/api/v1/avatars/{user.pk}/?v=")
    assert key not in avatar_url


@pytest.mark.django_db
def test_uploaded_photo_is_served_without_authentication(
    user: User, avatar_media: Path
) -> None:
    avatar_url = _client(user).post(AVATAR_URL, {"avatar": _png()}).json()["avatar_url"]

    response = APIClient().get(avatar_url)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content) == PNG_BYTES
    assert stored_files(avatar_media)


@pytest.mark.django_db
@pytest.mark.usefixtures("avatar_media")
def test_serving_returns_404_without_an_uploaded_photo(user: User) -> None:
    UserProfile.objects.create(user=user, avatar_url="https://example.com/me.jpg")

    missing_user = APIClient().get(
        "/api/v1/avatars/00000000-0000-0000-0000-000000000001/"
    )
    link_only = APIClient().get(f"/api/v1/avatars/{user.pk}/")

    assert missing_user.status_code == 404
    assert link_only.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_replacing_the_photo_deletes_the_previous_file(
    user: User, avatar_media: Path
) -> None:
    client = _client(user)
    first_url = client.post(AVATAR_URL, {"avatar": _png()}).json()["avatar_url"]
    first_key = _key(user)

    second_url = client.post(AVATAR_URL, {"avatar": _png()}).json()["avatar_url"]

    assert second_url != first_url
    assert not (avatar_media / first_key).exists()
    assert stored_files(avatar_media) == [avatar_media / _key(user)]


@pytest.mark.django_db(transaction=True)
def test_delete_removes_the_photo_and_any_link(user: User, avatar_media: Path) -> None:
    client = _client(user)
    client.post(AVATAR_URL, {"avatar": _png()})

    response = client.delete(AVATAR_URL)

    assert response.status_code == 200
    assert response.json()["avatar_url"] == ""
    profile = UserProfile.objects.get(user=user)
    assert (profile.avatar_object_key, profile.avatar_url) == ("", "")
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("avatar_media")
def test_upload_replaces_a_pasted_link(user: User) -> None:
    UserProfile.objects.create(user=user, avatar_url="https://example.com/me.jpg")

    _client(user).post(AVATAR_URL, {"avatar": _png()})

    profile = UserProfile.objects.get(user=user)
    assert profile.avatar_url == ""
    assert profile.avatar_object_key


@pytest.mark.django_db(transaction=True)
def test_pasting_a_link_replaces_the_uploaded_photo(
    user: User, avatar_media: Path
) -> None:
    client = _client(user)
    client.post(AVATAR_URL, {"avatar": _png()})

    response = client.patch(
        PROFILE_URL, {"avatar_url": "https://example.com/me.jpg"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["avatar_url"] == "https://example.com/me.jpg"
    assert _key(user) == ""
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
def test_upload_rejects_a_file_that_is_not_an_image(
    user: User, avatar_media: Path
) -> None:
    upload = _png(payload=b"<svg onload=alert(1)>")

    response = _client(user).post(AVATAR_URL, {"avatar": upload})

    assert response.status_code == 400
    assert "avatar" in response.json()
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("avatar_media")
def test_upload_rejects_a_mismatched_declared_type(user: User) -> None:
    response = _client(user).post(
        AVATAR_URL, {"avatar": _png(content_type="image/jpeg")}
    )

    assert response.status_code == 400
    assert "avatar" in response.json()


@pytest.mark.django_db
def test_upload_rejects_an_image_over_5_mb(user: User, avatar_media: Path) -> None:
    upload = _png(payload=PNG_BYTES + b"\x00" * MAX_IMAGE_BYTES)

    response = _client(user).post(AVATAR_URL, {"avatar": upload})

    assert response.status_code == 400
    assert "avatar" in response.json()
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
def test_upload_requires_the_avatar_field(user: User) -> None:
    response = _client(user).post(AVATAR_URL, {}, format="multipart")

    assert response.status_code == 400
    assert "avatar" in response.json()


def test_delete_profile_avatar_ignores_keys_outside_the_user_prefix(
    avatar_media: Path,
) -> None:
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    outsider = write_outsider_file(avatar_media)

    delete_profile_avatar(user_id, "action-evidence/keep.png")
    delete_profile_avatar(
        user_id, f"{PROFILE_AVATAR_PREFIX}/{user_id}/../../action-evidence/keep.png"
    )

    assert outsider.exists()


@pytest.mark.django_db
def test_deployed_service_stores_and_signs_the_photo_in_the_bucket(
    user: User, settings: Any, monkeypatch: Any
) -> None:
    uploads = use_fake_gcs(settings, monkeypatch)

    avatar_url = _client(user).post(AVATAR_URL, {"avatar": _png()}).json()["avatar_url"]
    response = APIClient().get(avatar_url)

    key = _key(user)
    assert uploads == {key: (PNG_BYTES, "image/png")}
    assert response.status_code == 302
    assert response["Location"] == f"https://storage.example/{key}?signed"


# --- avatar_url wherever a user appears ------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("avatar_media")
def test_me_and_clan_roster_include_the_photo(user: User) -> None:
    client = _client(user)
    avatar_url = client.post(AVATAR_URL, {"avatar": _png()}).json()["avatar_url"]
    clan = create_private_clan(name="Green Team", created_by=user)
    member = User.objects.create_user(email="beto@iteso.mx")
    ClanMembership.objects.create(user=member, clan=clan)

    me = client.get("/api/v1/users/me/").json()
    roster = client.get(f"/api/v1/clans/{clan.pk}/").json()["members"]

    assert me["avatar_url"] == avatar_url
    by_user = {row["user_id"]: row["avatar_url"] for row in roster}
    assert by_user == {str(user.pk): avatar_url, str(member.pk): ""}


@pytest.mark.django_db
def test_feed_author_and_ranking_include_the_photo(user: User) -> None:
    UserProfile.objects.create(
        user=user, total_points=10, avatar_url="https://example.com/me.jpg"
    )
    Post.objects.create(
        author=user, content="Hola", post_type=PostType.COMMUNITY_MILESTONE
    )
    client = _client(user)

    post = client.get("/api/v1/feed/").json()["results"][0]
    ranking = client.get("/api/v1/rankings/users/").json()["results"][0]

    assert post["author"]["avatar_url"] == "https://example.com/me.jpg"
    assert ranking["avatar_url"] == "https://example.com/me.jpg"


@pytest.mark.django_db
def test_clan_roster_query_count_does_not_grow_with_members(
    user: User, django_assert_num_queries: Any
) -> None:
    clan = create_private_clan(name="Green Team", created_by=user)
    client = _client(user)
    url = f"/api/v1/clans/{clan.pk}/"
    with CaptureQueriesContext(connection) as single_member:
        client.get(url)
    for index in range(5):
        member = User.objects.create_user(email=f"m{index}@iteso.mx")
        UserProfile.objects.create(user=member, avatar_url="https://example.com/m.jpg")
        ClanMembership.objects.create(user=member, clan=clan)

    with django_assert_num_queries(len(single_member)):
        response = client.get(url)

    assert len(response.json()["members"]) == 6
