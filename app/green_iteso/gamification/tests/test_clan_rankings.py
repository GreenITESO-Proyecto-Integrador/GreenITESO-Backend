"""Coverage for clan rankings under /api/v1/rankings/?type=."""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User

URL = "/api/v1/rankings/"


@pytest.fixture(name="api_client")
def api_client_fixture() -> APIClient:
    viewer = User.objects.create_user(email="viewer@iteso.mx", password="local-only")
    api_client = APIClient()
    api_client.force_authenticate(viewer)
    return api_client


def _clan(
    name: str,
    points: int,
    *,
    clan_type: str = Clan.ClanType.INSTITUTIONAL,
    privacy: str = Clan.Privacy.PUBLIC,
) -> Clan:
    return Clan.objects.create(
        name=name, type=clan_type, privacy=privacy, total_points=points
    )


def _rows(response: Any) -> list[tuple[str, int, int]]:
    return [
        (row["clan_name"], row["rank"], row["total_points"])
        for row in response.json()["results"]
    ]


@pytest.mark.django_db
def test_clan_ranking_requires_authentication() -> None:
    assert APIClient().get(URL, {"type": "INSTITUTIONAL"}).status_code == 401


@pytest.mark.django_db
def test_clan_ranking_rejects_a_missing_or_unknown_type(api_client: APIClient) -> None:
    assert api_client.get(URL).status_code == 400
    assert api_client.get(URL, {"type": "INDIVIDUAL"}).status_code == 400


@pytest.mark.django_db
def test_institutional_ranking_orders_by_points_and_ties_share_rank(
    api_client: APIClient,
) -> None:
    _clan("Arquitectura", 20)
    _clan("Software", 30)
    _clan("Diseño", 20)
    _clan("Las Ranas", 99, clan_type=Clan.ClanType.PRIVATE)

    response = api_client.get(URL, {"type": "INSTITUTIONAL"})

    assert response.status_code == 200
    assert response.json()["ranking_type"] == "INSTITUTIONAL"
    assert [(rank, points) for _, rank, points in _rows(response)] == [
        (1, 30),
        (2, 20),
        (2, 20),
    ]
    assert "Las Ranas" not in response.content.decode()
    assert response.json() == api_client.get(URL, {"type": "INSTITUTIONAL"}).json()


@pytest.mark.django_db
def test_private_clan_ranking_includes_invite_only_clans(
    api_client: APIClient,
) -> None:
    _clan("Eco Warriors", 15, clan_type=Clan.ClanType.PRIVATE)
    _clan(
        "Las Ranas",
        40,
        clan_type=Clan.ClanType.PRIVATE,
        privacy=Clan.Privacy.PRIVATE_INVITE,
    )
    _clan("Software", 100)

    response = api_client.get(URL, {"type": "PRIVATE_CLAN"})

    assert response.json()["ranking_type"] == "PRIVATE_CLAN"
    assert _rows(response) == [("Las Ranas", 1, 40), ("Eco Warriors", 2, 15)]


@pytest.mark.django_db
def test_dissolved_clans_are_excluded(api_client: APIClient) -> None:
    _clan("Software", 10)
    dissolved = _clan("Arquitectura", 99)
    dissolved.deleted_at = timezone.now()
    dissolved.save(update_fields=["deleted_at"])

    assert _rows(api_client.get(URL, {"type": "INSTITUTIONAL"})) == [
        ("Software", 1, 10)
    ]


@pytest.mark.django_db
def test_limit_offset_pages_keep_absolute_ranks(api_client: APIClient) -> None:
    for points in (50, 40, 30, 20, 10):
        _clan(f"Clan {points}", points)

    response = api_client.get(URL, {"type": "INSTITUTIONAL", "limit": 2, "offset": 2})

    body = response.json()
    assert body["count"] == 5
    assert _rows(response) == [("Clan 30", 3, 30), ("Clan 20", 4, 20)]


@pytest.mark.django_db
def test_clan_ranking_query_count_does_not_grow_with_rows(
    api_client: APIClient, django_assert_num_queries: Any
) -> None:
    for points in range(1, 21):
        _clan(f"Clan {points}", points)

    with django_assert_num_queries(2):
        response = api_client.get(URL, {"type": "INSTITUTIONAL", "limit": 20})

    assert len(response.json()["results"]) == 20
