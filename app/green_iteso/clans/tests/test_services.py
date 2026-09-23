"""Coverage for clans services."""

from __future__ import annotations

import threading

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    DuplicateClanLeaderError,
    MembershipClanMismatchError,
    assign_leader,
    create_clan,
    dissolve_clan,
)


@pytest.mark.django_db
def test_create_clan_sets_owner(leader: User) -> None:
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    assert clan.created_by == leader
    assert clan.type == Clan.ClanType.PRIVATE
    assert Clan.objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_create_clan_grants_creator_leader_membership(leader: User) -> None:
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )

    membership = ClanMembership.objects.get(user=leader, clan=clan)
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


@pytest.mark.django_db(transaction=True)
def test_assign_leader_blocks_concurrent_promotion_from_a_leaderless_clan() -> None:
    """Regression test: the clan starts with zero LEADER rows.

    This is the intermediate state of a leadership transfer (the old leader
    already demoted, the new one not yet promoted) and is the case the
    previous ``select_for_update`` on LEADER rows missed entirely, since
    there was no LEADER row for either concurrent caller to lock.
    """
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    challenger = User.objects.create_user(
        email="challenger@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=owner
    )
    owner_membership = ClanMembership.objects.get(user=owner, clan=clan)
    owner_membership.role = ClanMembership.MembershipRole.MEMBER
    owner_membership.save(update_fields=["role"])
    challenger_membership = ClanMembership.objects.create(
        user=challenger, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    assert (
        ClanMembership.objects.filter(
            clan=clan, role=ClanMembership.MembershipRole.LEADER
        ).count()
        == 0
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


@pytest.mark.django_db
def test_assign_leader_rejects_a_membership_from_a_different_clan() -> None:
    """Regression test for the exact mismatch the review demonstrated.

    ``clan_a`` is leaderless (so its own "already has a LEADER" check finds
    nothing to object to), and ``membership_in_b`` is a plain member of the
    *other* clan, which already has its own leader. Without the guard,
    promoting ``membership_in_b`` while checking against ``clan_a`` would
    silently give clan_b a second LEADER.
    """
    owner_a = User.objects.create_user(email="lead-a@iteso.mx", password="local-only")
    owner_b = User.objects.create_user(email="lead-b@iteso.mx", password="local-only")
    candidate_b = User.objects.create_user(
        email="candidate-b@iteso.mx", password="local-only"
    )
    clan_a = create_clan(
        name="Clan A", clan_type=Clan.ClanType.PRIVATE, created_by=owner_a
    )
    clan_b = create_clan(
        name="Clan B", clan_type=Clan.ClanType.PRIVATE, created_by=owner_b
    )
    owner_a_membership = ClanMembership.objects.get(user=owner_a, clan=clan_a)
    owner_a_membership.role = ClanMembership.MembershipRole.MEMBER
    owner_a_membership.save(update_fields=["role"])
    membership_in_b = ClanMembership.objects.create(
        user=candidate_b, clan=clan_b, role=ClanMembership.MembershipRole.MEMBER
    )

    with pytest.raises(MembershipClanMismatchError):
        assign_leader(clan=clan_a, membership=membership_in_b)

    membership_in_b.refresh_from_db()
    assert membership_in_b.role == ClanMembership.MembershipRole.MEMBER
    assert (
        ClanMembership.objects.filter(
            clan=clan_b, role=ClanMembership.MembershipRole.LEADER
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_dissolve_clan_marks_it_as_deleted(leader: User, clan: Clan) -> None:
    dissolved = dissolve_clan(clan=clan, actor=leader)

    assert dissolved.deleted_at is not None
    assert Clan.all_objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_dissolve_clan_preserves_points_and_memberships(
    leader: User, member: User, clan: Clan
) -> None:
    ClanMembership.objects.create(user=member, clan=clan)
    Clan.objects.filter(pk=clan.pk).update(total_points=120)
    clan.refresh_from_db()

    dissolve_clan(clan=clan, actor=leader)

    clan.refresh_from_db()
    assert clan.total_points == 120
    assert ClanMembership.objects.filter(clan=clan).count() == 2


@pytest.mark.django_db
def test_dissolve_clan_clears_active_private_selection(
    leader: User, clan: Clan
) -> None:
    ClanMembership.objects.filter(user=leader, clan=clan).update(is_active_private=True)

    dissolve_clan(clan=clan, actor=leader)

    membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert membership.is_active_private is False


@pytest.mark.django_db
def test_dissolve_clan_rejects_non_leader(member: User, clan: Clan) -> None:
    ClanMembership.objects.create(user=member, clan=clan)

    with pytest.raises(PermissionDenied):
        dissolve_clan(clan=clan, actor=member)

    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolve_clan_rejects_a_demoted_former_leader(
    leader: User, member: User, clan: Clan
) -> None:
    """A stale LEADER role at call time must not be enough on its own.

    Sequential counterpart to the concurrency test below: even without a
    race, ``dissolve_clan`` must check leadership against current state, not
    trust that ``actor`` is still the leader just because it once was.
    """
    ClanMembership.objects.filter(user=leader, clan=clan).update(
        role=ClanMembership.MembershipRole.MEMBER
    )
    ClanMembership.objects.create(
        user=member, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    with pytest.raises(PermissionDenied):
        dissolve_clan(clan=clan, actor=leader)

    clan.refresh_from_db()
    assert clan.deleted_at is None


@pytest.mark.django_db
def test_dissolve_clan_rejects_institutional_clan(leader: User) -> None:
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )
    ClanMembership.objects.create(
        user=leader,
        clan=institutional_clan,
        role=ClanMembership.MembershipRole.LEADER,
    )

    with pytest.raises(PermissionDenied):
        dissolve_clan(clan=institutional_clan, actor=leader)

    institutional_clan.refresh_from_db()
    assert institutional_clan.deleted_at is None


@pytest.mark.django_db
def test_dissolve_clan_keeps_the_first_deletion_timestamp(
    leader: User, clan: Clan
) -> None:
    first = dissolve_clan(clan=clan, actor=leader)
    second = dissolve_clan(clan=clan, actor=leader)

    assert first.deleted_at == second.deleted_at


@pytest.mark.django_db(transaction=True)
def test_dissolve_clan_serializes_against_concurrent_leadership_transfer() -> None:
    """Regression test for the TOCTOU the review caught in ``dissolve_clan``.

    T2-42 (leadership transfer) is not implemented yet, so this inlines a
    transfer that follows the same ``select_for_update`` discipline
    ``assign_leader`` already uses, racing it against a dissolve by the
    about-to-be-demoted leader. The clan row lock must serialize the two
    operations: whichever side's transaction commits first fully determines
    what the other one sees, so the clan can never end up dissolved by an
    actor who was not its LEADER at commit time.
    """
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    successor = User.objects.create_user(
        email="successor@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
    leader_membership = ClanMembership.objects.get(user=leader, clan=clan)
    successor_membership = ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    outcomes: dict[str, str] = {}

    def transfer_leadership() -> None:
        with transaction.atomic():
            Clan.all_objects.select_for_update().get(pk=clan.pk)
            leader_membership.role = ClanMembership.MembershipRole.MEMBER
            leader_membership.save(update_fields=["role"])
            successor_membership.role = ClanMembership.MembershipRole.LEADER
            successor_membership.save(update_fields=["role"])
        outcomes["transfer"] = "done"

    def dissolve() -> None:
        try:
            dissolve_clan(clan=clan, actor=leader)
            outcomes["dissolve"] = "dissolved"
        except PermissionDenied:
            outcomes["dissolve"] = "rejected"

    threads = [
        threading.Thread(target=transfer_leadership),
        threading.Thread(target=dissolve),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes["transfer"] == "done"
    clan.refresh_from_db()
    if outcomes["dissolve"] == "dissolved":
        # dissolve won the row lock: it ran while leader was still LEADER.
        assert clan.deleted_at is not None
    else:
        # transfer won the row lock: leader was already demoted by the time
        # dissolve's lock acquisition unblocked and it re-checked.
        assert clan.deleted_at is None
        leader_membership.refresh_from_db()
        assert leader_membership.role == ClanMembership.MembershipRole.MEMBER
