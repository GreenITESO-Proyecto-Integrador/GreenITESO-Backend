"""Coverage for clans services."""

from __future__ import annotations

import threading

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    AlreadyLeadingAClanError,
    DuplicateClanLeaderError,
    DuplicateClanNameError,
    InvalidClanPrivacyError,
    MembershipClanMismatchError,
    PrivateClanLimitExceededError,
    accept_join_request,
    assign_institutional_clan,
    assign_leader,
    create_clan,
    create_private_clan,
    dissolve_clan,
    join_clan,
    leave_clan,
    reject_join_request,
    select_active_private_clan,
    transfer_leadership,
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
def test_transfer_leadership_promotes_successor_and_demotes_previous_leader(
    leader: User, successor: User, clan: Clan
) -> None:
    ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    promoted = transfer_leadership(clan=clan, actor=leader, successor=successor)

    assert promoted.role == ClanMembership.MembershipRole.LEADER
    previous_leader = ClanMembership.objects.get(user=leader, clan=clan)
    assert previous_leader.role == ClanMembership.MembershipRole.MEMBER
    assert (
        ClanMembership.objects.filter(
            clan=clan, role=ClanMembership.MembershipRole.LEADER
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_transfer_leadership_rejects_a_non_leader_actor(
    leader: User, member: User, successor: User, clan: Clan
) -> None:
    ClanMembership.objects.create(
        user=member, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    with pytest.raises(PermissionDenied):
        transfer_leadership(clan=clan, actor=member, successor=successor)

    leader_membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert leader_membership.role == ClanMembership.MembershipRole.LEADER


@pytest.mark.django_db
def test_transfer_leadership_rejects_a_non_member_successor(
    leader: User, clan: Clan
) -> None:
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        transfer_leadership(clan=clan, actor=leader, successor=outsider)

    leader_membership = ClanMembership.objects.get(user=leader, clan=clan)
    assert leader_membership.role == ClanMembership.MembershipRole.LEADER


@pytest.mark.django_db
def test_transfer_leadership_rejects_transferring_to_self(
    leader: User, clan: Clan
) -> None:
    with pytest.raises(ValueError):
        transfer_leadership(clan=clan, actor=leader, successor=leader)


@pytest.mark.django_db(transaction=True)
def test_transfer_leadership_serializes_against_a_concurrent_transfer(
    leader: User, clan: Clan
) -> None:
    """Two callers race to transfer leadership away from the same leader.

    The clan-row lock in ``transfer_leadership`` must serialize them: only
    the first to commit finds ``actor`` still LEADER, so exactly one
    transfer succeeds and the clan never ends up leaderless or with two
    LEADER rows.
    """
    first_candidate = User.objects.create_user(
        email="first@iteso.mx", password="local-only"
    )
    second_candidate = User.objects.create_user(
        email="second@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=first_candidate, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    ClanMembership.objects.create(
        user=second_candidate, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    outcomes: list[Exception | None] = [None, None]

    def transfer(index: int, successor: User) -> None:
        try:
            transfer_leadership(clan=clan, actor=leader, successor=successor)
        except PermissionDenied as exc:
            outcomes[index] = exc

    threads = [
        threading.Thread(target=transfer, args=(0, first_candidate)),
        threading.Thread(target=transfer, args=(1, second_candidate)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    leader_count = ClanMembership.objects.filter(
        clan=clan, role=ClanMembership.MembershipRole.LEADER
    ).count()
    assert leader_count == 1
    assert sum(1 for outcome in outcomes if outcome is not None) == 1


@pytest.mark.django_db
def test_select_active_private_clan_marks_the_membership_active() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=user
    )

    membership = select_active_private_clan(user=user, clan=clan)

    assert membership.is_active_private is True


@pytest.mark.django_db
def test_select_active_private_clan_deactivates_the_previous_selection() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    other_owner = User.objects.create_user(
        email="other@iteso.mx", password="local-only"
    )
    first_clan = create_clan(
        name="First Team", clan_type=Clan.ClanType.PRIVATE, created_by=user
    )
    second_clan = create_clan(
        name="Second Team", clan_type=Clan.ClanType.PRIVATE, created_by=other_owner
    )
    ClanMembership.objects.create(user=user, clan=second_clan)

    select_active_private_clan(user=user, clan=first_clan)
    select_active_private_clan(user=user, clan=second_clan)

    first_membership = ClanMembership.objects.get(user=user, clan=first_clan)
    second_membership = ClanMembership.objects.get(user=user, clan=second_clan)
    assert first_membership.is_active_private is False
    assert second_membership.is_active_private is True


@pytest.mark.django_db
def test_select_active_private_clan_rejects_an_institutional_clan() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )
    ClanMembership.objects.create(user=user, clan=institutional_clan)

    with pytest.raises(ValueError):
        select_active_private_clan(user=user, clan=institutional_clan)


@pytest.mark.django_db
def test_select_active_private_clan_rejects_a_non_member() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    other_owner = User.objects.create_user(
        email="other@iteso.mx", password="local-only"
    )
    clan = create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=other_owner
    )

    with pytest.raises(ValueError):
        select_active_private_clan(user=user, clan=clan)


@pytest.mark.django_db(transaction=True)
def test_select_active_private_clan_serializes_against_concurrent_selections() -> None:
    """Two concurrent selections for the same user must never leave two actives.

    The lock here is on the ``User`` row (a per-user invariant), not per-clan,
    so this races two *different* target clans rather than two callers on the
    same one: that's the actual scenario the lock has to close -- both calls
    passing their own unlocked "clear the previous active" read before either
    commits, which would otherwise leave both memberships marked active and
    violate ``membership_one_active_private_per_user``.
    """
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    other_owner = User.objects.create_user(
        email="other@iteso.mx", password="local-only"
    )
    first_clan = create_clan(
        name="First Team", clan_type=Clan.ClanType.PRIVATE, created_by=user
    )
    second_clan = create_clan(
        name="Second Team", clan_type=Clan.ClanType.PRIVATE, created_by=other_owner
    )
    ClanMembership.objects.create(user=user, clan=second_clan)

    def select(clan: Clan) -> None:
        select_active_private_clan(user=user, clan=clan)

    threads = [
        threading.Thread(target=select, args=(first_clan,)),
        threading.Thread(target=select, args=(second_clan,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    active_count = ClanMembership.objects.filter(
        user=user, is_active_private=True
    ).count()
    assert active_count == 1


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

    # Named apart from the imported ``transfer_leadership`` service: this
    # helper swaps the roles directly through the ORM to race the row lock,
    # and shadowing the service here would hide which one a reader is seeing.
    def transfer_via_orm() -> None:
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
        threading.Thread(target=transfer_via_orm),
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


class CreatePrivateClanTests(TestCase):
    """Tests for services.create_private_clan (T2-31)."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="creator@iteso.mx", password="pass1234"
        )

    def test_creates_clan_and_leader_membership(self) -> None:
        clan = create_private_clan(name="Los Rayos", created_by=self.user)

        self.assertEqual(clan.type, Clan.ClanType.PRIVATE)
        self.assertEqual(clan.privacy, Clan.Privacy.PUBLIC)
        membership = ClanMembership.objects.get(user=self.user, clan=clan)
        self.assertEqual(membership.role, ClanMembership.MembershipRole.LEADER)

    def test_accepts_explicit_privacy_and_optional_fields(self) -> None:
        clan = create_private_clan(
            name="Los Ocultos",
            created_by=self.user,
            description="secret squad",
            avatar_object_key="avatars/x.png",
            privacy=Clan.Privacy.PRIVATE_INVITE,
        )

        self.assertEqual(clan.privacy, Clan.Privacy.PRIVATE_INVITE)
        self.assertEqual(clan.description, "secret squad")
        self.assertEqual(clan.avatar_object_key, "avatars/x.png")

    def test_duplicate_name_raises(self) -> None:
        create_private_clan(name="Duplicado", created_by=self.user)
        other_user = User.objects.create_user(
            email="other@iteso.mx", password="pass1234"
        )

        with self.assertRaises(DuplicateClanNameError):
            create_private_clan(name="Duplicado", created_by=other_user)

    def test_already_leading_a_clan_raises(self) -> None:
        create_private_clan(name="Primero", created_by=self.user)

        with self.assertRaises(AlreadyLeadingAClanError):
            create_private_clan(name="Segundo", created_by=self.user)

    def test_private_clan_limit_exceeded_raises(self) -> None:
        # user reaches the 5-private-clan membership cap as a plain MEMBER
        # of five different clans (never LEADER, so BR-04's leadership rule
        # doesn't get in the way of testing the count rule in isolation).
        for i in range(5):
            other_leader = User.objects.create_user(
                email=f"leader{i}@iteso.mx", password="pass1234"
            )
            clan = create_private_clan(name=f"Clan {i}", created_by=other_leader)
            ClanMembership.objects.create(
                user=self.user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
            )

        with self.assertRaises(PrivateClanLimitExceededError):
            create_private_clan(name="Sexto", created_by=self.user)

    def test_rejects_invalid_privacy_value(self) -> None:
        with self.assertRaises(InvalidClanPrivacyError):
            create_private_clan(name="Bad", created_by=self.user, privacy="NOT_REAL")


@pytest.mark.django_db
def test_join_clan_joins_public_clan_directly(clan: Clan) -> None:
    """``clan`` fixture (via create_clan) defaults to Privacy.PUBLIC."""
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    membership = join_clan(clan=clan, user=applicant)

    assert membership.status == ClanMembership.Status.ACCEPTED
    assert ClanMembership.objects.filter(
        user=applicant, clan=clan, status=ClanMembership.Status.ACCEPTED
    ).exists()


@pytest.mark.django_db
def test_join_clan_creates_a_pending_request_for_a_private_invite_clan(
    leader: User,
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    membership = join_clan(clan=invite_clan, user=applicant)

    assert membership.status == ClanMembership.Status.PENDING


@pytest.mark.django_db
def test_join_clan_rejects_a_second_request_while_pending(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(ValueError):
        join_clan(clan=invite_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_rejects_an_already_accepted_member(clan: Clan) -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=clan, user=applicant)

    with pytest.raises(ValueError):
        join_clan(clan=clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_rejects_an_institutional_clan() -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )

    with pytest.raises(ValueError):
        join_clan(clan=institutional_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_enforces_br04_cap_for_a_public_clan() -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    for i in range(5):
        other_leader = User.objects.create_user(
            email=f"leader{i}@iteso.mx", password="local-only"
        )
        full_clan = create_private_clan(name=f"Clan {i}", created_by=other_leader)
        ClanMembership.objects.create(
            user=applicant, clan=full_clan, role=ClanMembership.MembershipRole.MEMBER
        )
    sixth_leader = User.objects.create_user(
        email="sixth@iteso.mx", password="local-only"
    )
    sixth_clan = create_private_clan(name="Sixth", created_by=sixth_leader)

    with pytest.raises(PrivateClanLimitExceededError):
        join_clan(clan=sixth_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_lets_a_rejected_applicant_request_again(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)
    reject_join_request(clan=invite_clan, actor=leader, applicant=applicant)

    membership = join_clan(clan=invite_clan, user=applicant)

    assert membership.status == ClanMembership.Status.PENDING


@pytest.mark.django_db
def test_accept_join_request_accepts_a_pending_applicant(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    membership = accept_join_request(
        clan=invite_clan, actor=leader, applicant=applicant
    )

    assert membership.status == ClanMembership.Status.ACCEPTED


@pytest.mark.django_db
def test_accept_join_request_rejects_a_non_leader_actor(
    leader: User, member: User
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=invite_clan)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(PermissionDenied):
        accept_join_request(clan=invite_clan, actor=member, applicant=applicant)


@pytest.mark.django_db
def test_accept_join_request_rejects_a_missing_pending_request(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        accept_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_accept_join_request_enforces_br04_cap_on_the_applicant(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)
    for i in range(5):
        other_leader = User.objects.create_user(
            email=f"leader{i}@iteso.mx", password="local-only"
        )
        full_clan = create_private_clan(name=f"Clan {i}", created_by=other_leader)
        ClanMembership.objects.create(
            user=applicant, clan=full_clan, role=ClanMembership.MembershipRole.MEMBER
        )

    with pytest.raises(PrivateClanLimitExceededError):
        accept_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_reject_join_request_marks_it_rejected(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    membership = reject_join_request(
        clan=invite_clan, actor=leader, applicant=applicant
    )

    assert membership.status == ClanMembership.Status.REJECTED


@pytest.mark.django_db
def test_reject_join_request_rejects_a_non_leader_actor(
    leader: User, member: User
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=invite_clan)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(PermissionDenied):
        reject_join_request(clan=invite_clan, actor=member, applicant=applicant)


@pytest.mark.django_db
def test_reject_join_request_rejects_a_missing_pending_request(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        reject_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_leave_clan_removes_the_callers_membership(member: User, clan: Clan) -> None:
    ClanMembership.objects.create(user=member, clan=clan)

    leave_clan(clan=clan, user=member)

    assert not ClanMembership.objects.filter(user=member, clan=clan).exists()


@pytest.mark.django_db
def test_leave_clan_rejects_the_leader(leader: User, clan: Clan) -> None:
    with pytest.raises(PermissionDenied):
        leave_clan(clan=clan, user=leader)

    assert ClanMembership.objects.filter(user=leader, clan=clan).exists()


@pytest.mark.django_db
def test_leave_clan_rejects_a_non_member(clan: Clan) -> None:
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        leave_clan(clan=clan, user=outsider)
