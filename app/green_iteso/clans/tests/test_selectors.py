"""Coverage for clans selectors."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan
from green_iteso.clans.selectors import list_active_clans


@pytest.mark.django_db
def test_list_active_clans_excludes_deleted() -> None:
    Clan.objects.create(name="Zeta", type=Clan.ClanType.INSTITUTIONAL)
    active = Clan.objects.create(name="Alpha", type=Clan.ClanType.INSTITUTIONAL)
    Clan.objects.create(
        name="Deleted",
        type=Clan.ClanType.PRIVATE,
        deleted_at="2026-01-01T00:00:00Z",
    )

    names = list(list_active_clans().values_list("name", flat=True))

    assert names == ["Alpha", "Zeta"]
    assert active.name in names


@pytest.mark.django_db
def test_list_active_clans_orders_by_points_desc_then_name() -> None:
    Clan.objects.create(name="Bravo", type=Clan.ClanType.INSTITUTIONAL, total_points=10)
    high = Clan.objects.create(
        name="Charlie", type=Clan.ClanType.INSTITUTIONAL, total_points=50
    )
    Clan.objects.create(name="Alpha", type=Clan.ClanType.INSTITUTIONAL, total_points=10)

    names = list(list_active_clans().values_list("name", flat=True))

    assert names == [high.name, "Alpha", "Bravo"]


@pytest.mark.django_db
def test_list_active_clans_filters_by_search_case_insensitive() -> None:
    Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    Clan.objects.create(name="Blue Squad", type=Clan.ClanType.PRIVATE)

    names = list(list_active_clans(search="green").values_list("name", flat=True))

    assert names == ["Green Team"]
