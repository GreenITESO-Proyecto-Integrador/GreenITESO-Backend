"""Coverage for clans services."""

from __future__ import annotations

import threading

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    DuplicateClanLeaderError,
    assign_leader,
    create_clan,
)


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
def test_assign_leader_promotes_membership() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    successor = User.objects.create_user(
        email="successor@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    old_leader = ClanMembership.objects.get(user=owner, clan=clan)
    old_leader.role = ClanMembership.MembershipRole.MEMBER
    old_leader.save(update_fields=["role"])
    new_membership = ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    promoted = assign_leader(clan=clan, membership=new_membership)

    assert promoted.role == ClanMembership.MembershipRole.LEADER


@pytest.mark.django_db
def test_assign_leader_rejects_second_leader_for_same_clan() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    candidate = User.objects.create_user(
        email="candidate@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    candidate_membership = ClanMembership.objects.create(
        user=candidate, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    with pytest.raises(DuplicateClanLeaderError):
        assign_leader(clan=clan, membership=candidate_membership)


@pytest.mark.django_db(transaction=True)
def test_assign_leader_blocks_concurrent_duplicate_leader_assignment() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    challenger = User.objects.create_user(
        email="challenger@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    owner_membership = ClanMembership.objects.get(user=owner, clan=clan)
    challenger_membership = ClanMembership.objects.create(
        user=challenger, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    results: list[Exception | None] = [None, None]

    def promote(index: int, membership: ClanMembership) -> None:
        try:
            assign_leader(clan=clan, membership=membership)
        except DuplicateClanLeaderError as exc:
            results[index] = exc

    threads = [
        threading.Thread(target=promote, args=(0, owner_membership)),
        threading.Thread(target=promote, args=(1, challenger_membership)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    leader_count = ClanMembership.objects.filter(
        clan=clan, role=ClanMembership.MembershipRole.LEADER
    ).count()
    assert leader_count == 1
    assert sum(1 for result in results if result is not None) == 1
