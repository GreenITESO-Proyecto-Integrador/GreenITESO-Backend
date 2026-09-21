"""Coverage for clans services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import create_clan


@pytest.mark.django_db
def test_create_clan_sets_owner() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )

    assert clan.created_by == owner
    assert clan.type == Clan.ClanType.PRIVATE
    assert Clan.objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_create_clan_grants_creator_leader_membership() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )

    membership = ClanMembership.objects.get(user=owner, clan=clan)
    assert membership.role == ClanMembership.MembershipRole.LEADER
