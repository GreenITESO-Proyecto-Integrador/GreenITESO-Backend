"""Frames and backgrounds shown with an author in the feed and a clan roster."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from green_iteso.accounts.models import User, UserProfile
from green_iteso.actions.models import ExchangeableItem
from green_iteso.clans.services import create_private_clan, join_clan
from green_iteso.feed.models import Post, PostType

FRAME_URL = "https://cdn.example.com/frames/halloween.png"
BACKGROUND_URL = "https://cdn.example.com/backgrounds/grasoso.png"
TRACKER_URL = "https://tracker.example.com/pixel.png"


@pytest.fixture(autouse=True)
def catalog() -> None:
    """Point the two demo items a migration seeds at known images."""
    for key, category, url in (
        ("frame_halloween", ExchangeableItem.Category.FRAME, FRAME_URL),
        ("background_grasoso", ExchangeableItem.Category.BACKGROUND, BACKGROUND_URL),
    ):
        ExchangeableItem.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "category": category,
                "points_cost": 10,
                "image_url": url,
            },
        )


def _user(email: str, **preferences: object) -> User:
    user = User.objects.create_user(email=email, password="local-only")
    UserProfile.objects.update_or_create(
        user=user, defaults={"preferences": preferences}
    )
    return user


def _feed_author(author: User) -> dict[str, str]:
    post = Post.objects.create(
        author=author, post_type=PostType.SHARED_EVIDENCE, content="Reciclé hoy."
    )
    response = APIClient().get(f"/api/v1/feed/{post.pk}/")
    assert response.status_code == 200
    return response.json()["author"]


@pytest.mark.django_db
def test_feed_author_carries_the_catalog_images_of_the_active_cosmetics() -> None:
    author = _user(
        "ana@iteso.mx",
        active_frame="frame_halloween",
        active_background="background_grasoso",
    )

    payload = _feed_author(author)

    assert payload["frame_url"] == FRAME_URL
    assert payload["background_url"] == BACKGROUND_URL


@pytest.mark.django_db
def test_feed_author_without_cosmetics_gets_empty_urls() -> None:
    payload = _feed_author(_user("ana@iteso.mx"))

    assert payload["frame_url"] == ""
    assert payload["background_url"] == ""


@pytest.mark.django_db
def test_a_url_stored_in_preferences_is_never_served_to_others() -> None:
    author = _user(
        "ana@iteso.mx",
        active_frame="not_in_the_catalog",
        active_frame_url=TRACKER_URL,
        active_background_url=TRACKER_URL,
    )

    payload = _feed_author(author)

    assert payload["frame_url"] == ""
    assert payload["background_url"] == ""


@pytest.mark.django_db
def test_a_key_of_the_wrong_kind_shows_nothing() -> None:
    author = _user(
        "ana@iteso.mx",
        active_frame="background_grasoso",
        active_background="frame_halloween",
    )

    payload = _feed_author(author)

    assert payload["frame_url"] == ""
    assert payload["background_url"] == ""


@pytest.mark.django_db
@pytest.mark.parametrize("value", [None, 7, ["frame_halloween"], ""])
def test_a_malformed_preference_value_shows_nothing(value: object) -> None:
    payload = _feed_author(_user("ana@iteso.mx", active_frame=value))

    assert payload["frame_url"] == ""


@pytest.mark.django_db
def test_clan_roster_carries_each_members_cosmetics() -> None:
    leader = _user("lead@iteso.mx", active_frame="frame_halloween")
    member = _user("member@iteso.mx", active_background="background_grasoso")
    plain = _user("plain@iteso.mx")
    clan = create_private_clan(created_by=leader, name="Verde")
    join_clan(clan=clan, user=member)
    join_clan(clan=clan, user=plain)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.get(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 200
    by_id = {row["user_id"]: row for row in response.json()["members"]}
    assert by_id[str(leader.pk)]["frame_url"] == FRAME_URL
    assert by_id[str(leader.pk)]["background_url"] == ""
    assert by_id[str(member.pk)]["frame_url"] == ""
    assert by_id[str(member.pk)]["background_url"] == BACKGROUND_URL
    assert by_id[str(plain.pk)]["frame_url"] == ""


@pytest.mark.django_db
def test_roster_queries_do_not_grow_with_the_number_of_members() -> None:
    leader = _user("lead@iteso.mx", active_frame="frame_halloween")
    clan = create_private_clan(created_by=leader, name="Verde")
    client = APIClient()
    client.force_authenticate(leader)

    def count_queries() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(f"/api/v1/clans/{clan.pk}/").status_code == 200
        return len(queries)

    baseline = count_queries()
    for number in range(4):
        newcomer = _user(
            f"member{number}@iteso.mx", active_background="background_grasoso"
        )
        join_clan(clan=clan, user=newcomer)

    assert count_queries() == baseline
