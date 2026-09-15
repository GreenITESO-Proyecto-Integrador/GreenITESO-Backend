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
