"""Coverage for clans services."""

from __future__ import annotations

import threading

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    DuplicateClanLeaderError,
    MembershipClanMismatchError,
    assign_institutional_clan,
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
def test_assign_institutional_clan_creates_it_on_first_use() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    profile = assign_institutional_clan(user=user, career="Ingeniería en Sistemas")

    clan = Clan.objects.get(name="Ingeniería en Sistemas")
    assert clan.type == Clan.ClanType.INSTITUTIONAL
    assert profile.institutional_clan == clan
    assert profile.career == "Ingeniería en Sistemas"
    assert profile.onboarding_completed_at is not None


@pytest.mark.django_db
def test_assign_institutional_clan_grants_member_role() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    profile = assign_institutional_clan(user=user, career="Ingeniería en Sistemas")

    membership = ClanMembership.objects.get(user=user, clan=profile.institutional_clan)
    assert membership.role == ClanMembership.MembershipRole.MEMBER


@pytest.mark.django_db
def test_assign_institutional_clan_reuses_the_same_career_clan() -> None:
    first_user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    second_user = User.objects.create_user(email="zoe@iteso.mx", password="local-only")

    first_profile = assign_institutional_clan(
        user=first_user, career="Diseño Industrial"
    )
    second_profile = assign_institutional_clan(
        user=second_user, career="Diseño Industrial"
    )

    assert first_profile.institutional_clan == second_profile.institutional_clan
    assert Clan.objects.filter(name="Diseño Industrial").count() == 1


@pytest.mark.django_db
def test_assign_institutional_clan_is_idempotent_for_the_same_career() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    assign_institutional_clan(user=user, career="Diseño Industrial")
    assign_institutional_clan(user=user, career="Diseño Industrial")

    assert ClanMembership.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_assign_institutional_clan_keeps_the_original_timestamp() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    first = assign_institutional_clan(user=user, career="Diseño Industrial")
    second = assign_institutional_clan(user=user, career="Mecatrónica")

    assert second.onboarding_completed_at == first.onboarding_completed_at
    assert second.career == "Mecatrónica"


@pytest.mark.django_db
def test_assign_institutional_clan_rejects_a_blank_career() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    with pytest.raises(ValueError):
        assign_institutional_clan(user=user, career="   ")


@pytest.mark.django_db
def test_assign_institutional_clan_rejects_a_name_taken_by_another_clan_type() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    Clan.objects.create(name="Diseño Industrial", type=Clan.ClanType.PRIVATE)

    with pytest.raises(ValueError):
        assign_institutional_clan(user=user, career="Diseño Industrial")


@pytest.mark.django_db
def test_assign_institutional_clan_removes_the_stale_membership_on_change() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    first_profile = assign_institutional_clan(user=user, career="Diseño Industrial")
    second_profile = assign_institutional_clan(user=user, career="Mecatrónica")

    memberships = ClanMembership.objects.filter(user=user)
    assert memberships.count() == 1
    assert memberships.get().clan == second_profile.institutional_clan
    assert first_profile.institutional_clan != second_profile.institutional_clan
