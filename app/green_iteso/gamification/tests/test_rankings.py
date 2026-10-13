"""Coverage for the user points ranking under /api/v1/rankings/users/."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User, UserProfile

URL = "/api/v1/rankings/users/"

MakeUser = Callable[..., User]


@pytest.fixture(name="make_user")
def make_user_fixture() -> MakeUser:
    created: list[User] = []

    def make_user(points: int, **profile_fields: Any) -> User:
        user_fields = {
            key: profile_fields.pop(key)
            for key in ("nickname", "first_name", "last_name", "is_active")
            if key in profile_fields
        }
        user = User.objects.create_user(
            email=f"user{len(created)}@iteso.mx", password="local-only", **user_fields
        )
        UserProfile.objects.create(user=user, total_points=points, **profile_fields)
        created.append(user)
        return user

    return make_user


@pytest.fixture(name="api_client")
def api_client_fixture() -> APIClient:
    viewer = User.objects.create_user(email="viewer@iteso.mx", password="local-only")
    api_client = APIClient()
    api_client.force_authenticate(viewer)
    return api_client


def _rows(response: Any) -> list[tuple[str, int, int]]:
    return [
        (row["display_name"], row["rank"], row["total_points"])
        for row in response.json()["results"]
    ]


@pytest.mark.django_db
def test_ranking_requires_authentication() -> None:
    assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db
def test_ranking_orders_by_points_and_ties_share_rank(
    api_client: APIClient, make_user: MakeUser
) -> None:
    make_user(10, nickname="c")
    make_user(30, nickname="a")
    make_user(20, nickname="b1")
    make_user(20, nickname="b2")

    response = api_client.get(URL)

    assert response.status_code == 200
    rows = _rows(response)
    assert [(rank, points) for _, rank, points in rows] == [
        (1, 30),
        (2, 20),
        (2, 20),
        (4, 10),
    ]
    # Ties keep a stable order (user id) so pages never shuffle.
    assert response.json() == api_client.get(URL).json()


@pytest.mark.django_db
def test_limit_offset_pages_keep_absolute_ranks(
    api_client: APIClient, make_user: MakeUser
) -> None:
    for points in (50, 40, 30, 20, 10):
        make_user(points, nickname=f"p{points}")

    response = api_client.get(URL, {"limit": 2, "offset": 2})

    body = response.json()
    assert body["count"] == 5
    assert _rows(response) == [("p30", 3, 30), ("p20", 4, 20)]
    assert "offset=4" in body["next"]
    assert body["previous"] is not None


@pytest.mark.django_db
def test_private_profiles_and_inactive_users_are_excluded(
    api_client: APIClient, make_user: MakeUser
) -> None:
    make_user(10, nickname="visible")
    make_user(99, nickname="hidden", visibility=UserProfile.Visibility.PRIVATE)
    make_user(98, nickname="inactive", is_active=False)

    assert _rows(api_client.get(URL)) == [("visible", 1, 10)]


@pytest.mark.django_db
def test_display_name_never_exposes_email(
    api_client: APIClient, make_user: MakeUser
) -> None:
    nick = make_user(30, nickname="Bici", first_name="Ana", last_name="LÃ³pez")
    named = make_user(20, first_name="Luis", last_name="PÃ©rez")
    anonymous = make_user(10)

    response = api_client.get(URL)

    assert [row["display_name"] for row in response.json()["results"]] == [
        "Bici",
        "Luis PÃ©rez",
        "Usuario GreenITESO",
    ]
    assert [row["id"] for row in response.json()["results"]] == [
        str(nick.id),
        str(named.id),
        str(anonymous.id),
    ]
    assert "@" not in response.content.decode()


@pytest.mark.django_db
def test_ranking_query_count_does_not_grow_with_rows(
    api_client: APIClient, make_user: MakeUser, django_assert_num_queries: Any
) -> None:
    for points in range(1, 21):
        make_user(points)

    # One COUNT for pagination plus one ranked page with the user joined.
    with django_assert_num_queries(2):
        response = api_client.get(URL, {"limit": 20})

    assert len(response.json()["results"]) == 20
