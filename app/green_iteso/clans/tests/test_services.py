"""Coverage for clans services."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import create_clan, dissolve_clan


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


@pytest.mark.django_db
def test_dissolve_clan_marks_it_as_deleted() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    dissolved = dissolve_clan(clan=clan, actor=leader)

    assert dissolved.deleted_at is not None
    assert Clan.objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_dissolve_clan_preserves_points_and_memberships() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.create(user=member, clan=clan)
    Clan.objects.filter(pk=clan.pk).update(total_points=120)
    clan.refresh_from_db()

    dissolve_clan(clan=clan, actor=leader)

    clan.refresh_from_db()
    assert clan.total_points == 120
    assert ClanMembership.objects.filter(clan=clan).count() == 2


@pytest.mark.django_db
def test_dissolve_clan_clears_active_private_selection() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.filter(user=leader, clan=clan).update(is_active_private=True)

    dissolve_clan(clan=clan, actor=leader)

    membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert membership.is_active_private is False


@pytest.mark.django_db
def test_dissolve_clan_rejects_non_leader() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    ClanMembership.objects.create(user=member, clan=clan)

    with pytest.raises(PermissionDenied):
        dissolve_clan(clan=clan, actor=member)

    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolve_clan_rejects_institutional_clan() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )
    ClanMembership.objects.create(
        user=leader, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    with pytest.raises(PermissionDenied):
        dissolve_clan(clan=clan, actor=leader)

    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolve_clan_keeps_the_first_deletion_timestamp() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    first = dissolve_clan(clan=clan, actor=leader)
    second = dissolve_clan(clan=clan, actor=leader)

    assert first.deleted_at == second.deleted_at
