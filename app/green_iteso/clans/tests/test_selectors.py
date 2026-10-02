"""Coverage for clans selectors."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.selectors import list_active_clans


@pytest.mark.django_db
def test_list_active_clans_excludes_deleted() -> None:
    viewer = User.objects.create_user(email="viewer@iteso.mx", password="local-only")
    Clan.objects.create(name="Zeta", type=Clan.ClanType.INSTITUTIONAL)
    active = Clan.objects.create(name="Alpha", type=Clan.ClanType.INSTITUTIONAL)
    Clan.objects.create(
        name="Deleted",
        type=Clan.ClanType.PRIVATE,
        deleted_at="2026-01-01T00:00:00Z",
    )

    names = list(list_active_clans(user=viewer).values_list("name", flat=True))

    assert names == ["Alpha", "Zeta"]
    assert active.name in names


@pytest.mark.django_db
def test_list_active_clans_orders_by_points_desc_then_name() -> None:
    viewer = User.objects.create_user(email="viewer@iteso.mx", password="local-only")
    Clan.objects.create(name="Bravo", type=Clan.ClanType.INSTITUTIONAL, total_points=10)
    high = Clan.objects.create(
        name="Charlie", type=Clan.ClanType.INSTITUTIONAL, total_points=50
    )
    Clan.objects.create(name="Alpha", type=Clan.ClanType.INSTITUTIONAL, total_points=10)

    names = list(list_active_clans(user=viewer).values_list("name", flat=True))

    assert names == [high.name, "Alpha", "Bravo"]


@pytest.mark.django_db
def test_list_active_clans_filters_by_search_case_insensitive() -> None:
    viewer = User.objects.create_user(email="viewer@iteso.mx", password="local-only")
    Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    Clan.objects.create(name="Blue Squad", type=Clan.ClanType.PRIVATE)

    names = list(
        list_active_clans(user=viewer, search="green").values_list("name", flat=True)
    )

    assert names == ["Green Team"]


@pytest.mark.django_db
def test_list_active_clans_hides_private_invite_clan_from_non_members() -> None:
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )
    leader = User.objects.create_user(email="leader@iteso.mx", password="local-only")
    clan = Clan.objects.create(
        name="Secret Clan",
        type=Clan.ClanType.PRIVATE,
        privacy=Clan.Privacy.PRIVATE_INVITE,
    )
    ClanMembership.objects.create(user=leader, clan=clan)

    names = list(list_active_clans(user=outsider).values_list("name", flat=True))

    assert "Secret Clan" not in names


@pytest.mark.django_db
def test_list_active_clans_shows_private_invite_clan_to_its_member() -> None:
    leader = User.objects.create_user(email="leader@iteso.mx", password="local-only")
    clan = Clan.objects.create(
        name="Secret Clan",
        type=Clan.ClanType.PRIVATE,
        privacy=Clan.Privacy.PRIVATE_INVITE,
    )
    ClanMembership.objects.create(user=leader, clan=clan)

    names = list(list_active_clans(user=leader).values_list("name", flat=True))

    assert "Secret Clan" in names
