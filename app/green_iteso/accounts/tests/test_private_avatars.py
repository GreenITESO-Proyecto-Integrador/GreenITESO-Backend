"""A PRIVATE profile's photo follows its real name: only the person and admins see it (#238)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import ClanMembership, User, UserProfile
from green_iteso.clans.services import create_private_clan
from green_iteso.core.tests.image_helpers import png_upload, use_local_media
from green_iteso.feed.models import Post, PostType

AVATAR_URL = "/api/v1/profile/me/avatar/"
PRIVATE = UserProfile.Visibility.PRIVATE
PUBLIC = UserProfile.Visibility.PUBLIC


@pytest.fixture(autouse=True)
def avatar_media(settings: Any, tmp_path: Path) -> Path:
    return use_local_media(settings, tmp_path)


@pytest.fixture
def owner() -> User:
    return User.objects.create_user(
        email="ana@iteso.mx", first_name="Ana", nickname="Anita"
    )


@pytest.fixture
def other() -> User:
    return User.objects.create_user(email="beto@iteso.mx", first_name="Beto")


@pytest.fixture
def admin() -> User:
    return User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _upload(user: User, visibility: str) -> str:
    """Upload a photo for ``user``, set the visibility; return the URL the owner gets."""
    _client(user).post(AVATAR_URL, {"avatar": png_upload()})
    UserProfile.objects.filter(user=user).update(visibility=visibility)
    return _client(user).get("/api/v1/profile/me/").json()["avatar_url"]


def _fetch(url: str) -> int:
    parts = urlsplit(url)
    return APIClient().get(parts.path, parse_qs(parts.query)).status_code


def _post_by(author: User) -> None:
    Post.objects.create(
        author=author, content="Hola", post_type=PostType.COMMUNITY_MILESTONE
    )


@pytest.mark.django_db
def test_owner_gets_a_signed_url_that_loads(owner: User) -> None:
    url = _upload(owner, PRIVATE)

    assert "sig" in parse_qs(urlsplit(url).query)
    assert _fetch(url) == 200


@pytest.mark.django_db
def test_private_photo_is_404_without_a_valid_signature(owner: User) -> None:
    url = _upload(owner, PRIVATE)
    path = urlsplit(url).path
    version = parse_qs(urlsplit(url).query)["v"][0]

    assert APIClient().get(path, {"v": version}).status_code == 404
    assert APIClient().get(path, {"v": version, "sig": "forged"}).status_code == 404


@pytest.mark.django_db
def test_a_signature_for_another_user_does_not_unlock_the_photo(
    owner: User, other: User
) -> None:
    _upload(owner, PRIVATE)
    other_sig = parse_qs(urlsplit(_upload(other, PRIVATE)).query)["sig"][0]

    response = APIClient().get(f"/api/v1/avatars/{owner.pk}/", {"sig": other_sig})

    assert response.status_code == 404


@pytest.mark.django_db
def test_a_new_upload_invalidates_the_previous_signed_url(owner: User) -> None:
    first = _upload(owner, PRIVATE)
    second = _upload(owner, PRIVATE)

    assert _fetch(first) == 404
    assert _fetch(second) == 200


@pytest.mark.django_db
def test_turning_a_profile_private_closes_its_old_public_url(owner: User) -> None:
    public_url = _upload(owner, PUBLIC)
    assert "sig" not in parse_qs(urlsplit(public_url).query)
    assert _fetch(public_url) == 200

    UserProfile.objects.filter(user=owner).update(visibility=PRIVATE)

    assert _fetch(public_url) == 404


@pytest.mark.django_db
def test_feed_hides_a_private_authors_photo_from_others(
    owner: User, other: User
) -> None:
    _upload(owner, PRIVATE)
    _post_by(owner)

    seen_by_other = _client(other).get("/api/v1/feed/").json()["results"][0]
    seen_by_owner = _client(owner).get("/api/v1/feed/").json()["results"][0]

    assert seen_by_other["author"]["avatar_url"] == ""
    assert _fetch(seen_by_owner["author"]["avatar_url"]) == 200


@pytest.mark.django_db
def test_feed_keeps_a_public_authors_photo(owner: User, other: User) -> None:
    url = _upload(owner, PUBLIC)
    _post_by(owner)

    post = _client(other).get("/api/v1/feed/").json()["results"][0]

    assert post["author"]["avatar_url"] == url


@pytest.mark.django_db
def test_clan_roster_hides_a_private_members_photo_from_others(
    owner: User, other: User
) -> None:
    _upload(owner, PRIVATE)
    clan = create_private_clan(name="Green Team", created_by=other)
    ClanMembership.objects.create(user=owner, clan=clan)

    def roster_photo(viewer: User) -> str:
        members = _client(viewer).get(f"/api/v1/clans/{clan.pk}/").json()["members"]
        return {row["user_id"]: row["avatar_url"] for row in members}[str(owner.pk)]

    assert roster_photo(other) == ""
    assert _fetch(roster_photo(owner)) == 200


@pytest.mark.django_db
def test_ranking_never_lists_a_private_user_so_never_shows_the_photo(
    owner: User, other: User
) -> None:
    _upload(owner, PRIVATE)
    _upload(other, PUBLIC)
    UserProfile.objects.filter(user__in=[owner, other]).update(total_points=50)

    rows = _client(other).get("/api/v1/rankings/users/").json()["results"]

    by_user = {row["id"]: row["avatar_url"] for row in rows}
    assert str(owner.pk) not in by_user
    assert by_user[str(other.pk)]


@pytest.mark.django_db
def test_a_private_pasted_link_is_hidden_from_others(owner: User, other: User) -> None:
    UserProfile.objects.create(
        user=owner, visibility=PRIVATE, avatar_url="https://example.com/ana.jpg"
    )
    _post_by(owner)

    seen_by_other = _client(other).get("/api/v1/feed/").json()["results"][0]
    seen_by_owner = _client(owner).get("/api/v1/feed/").json()["results"][0]

    assert seen_by_other["author"]["avatar_url"] == ""
    assert seen_by_owner["author"]["avatar_url"] == "https://example.com/ana.jpg"


@pytest.mark.django_db
def test_admins_still_see_a_private_photo(owner: User, admin: User) -> None:
    _upload(owner, PRIVATE)
    _post_by(owner)

    post = _client(admin).get("/api/v1/feed/").json()["results"][0]

    assert _fetch(post["author"]["avatar_url"]) == 200
