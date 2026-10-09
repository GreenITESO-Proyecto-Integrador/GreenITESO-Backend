"""Coverage for the reusable RBAC permission classes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.core.permissions import IsAdmin, IsClanLeader, IsSelfOrAdmin


def _request(user: User | None) -> SimpleNamespace:
    return SimpleNamespace(user=user)


@pytest.mark.django_db
def test_is_admin_allows_admin_role() -> None:
    admin = User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)

    assert IsAdmin().has_permission(_request(admin), None) is True


@pytest.mark.django_db
def test_is_admin_denies_non_admin_role() -> None:
    student = User.objects.create_user(email="student@iteso.mx")

    assert IsAdmin().has_permission(_request(student), None) is False


def test_is_admin_denies_unauthenticated() -> None:
    anonymous = SimpleNamespace(is_authenticated=False)

    assert IsAdmin().has_permission(_request(anonymous), None) is False


@pytest.mark.django_db
def test_is_self_or_admin_allows_owner() -> None:
    owner = User.objects.create_user(email="owner@iteso.mx")

    assert IsSelfOrAdmin().has_object_permission(_request(owner), None, owner) is True


@pytest.mark.django_db
def test_is_self_or_admin_allows_admin_for_other_users_object() -> None:
    admin = User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)
    other = User.objects.create_user(email="other@iteso.mx")

    assert IsSelfOrAdmin().has_object_permission(_request(admin), None, other) is True


@pytest.mark.django_db
def test_is_self_or_admin_denies_other_authenticated_user() -> None:
    caller = User.objects.create_user(email="caller@iteso.mx")
    other = User.objects.create_user(email="other@iteso.mx")

    assert IsSelfOrAdmin().has_object_permission(_request(caller), None, other) is False


@pytest.mark.django_db
def test_is_self_or_admin_resolves_owner_via_user_attribute() -> None:
    owner = User.objects.create_user(email="owner@iteso.mx")
    profile = UserProfile.objects.create(user=owner)

    assert IsSelfOrAdmin().has_object_permission(_request(owner), None, profile) is True


@pytest.mark.django_db
def test_is_clan_leader_allows_leader_of_the_clan() -> None:
    leader = User.objects.create_user(email="leader@iteso.mx")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=leader, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    assert IsClanLeader().has_object_permission(_request(leader), None, clan) is True


@pytest.mark.django_db
def test_is_clan_leader_allows_admin_regardless_of_membership() -> None:
    admin = User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)

    assert IsClanLeader().has_object_permission(_request(admin), None, clan) is True


@pytest.mark.django_db
def test_is_clan_leader_denies_member_role() -> None:
    member = User.objects.create_user(email="member@iteso.mx")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=member, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )

    assert IsClanLeader().has_object_permission(_request(member), None, clan) is False


@pytest.mark.django_db
def test_is_clan_leader_denies_user_with_no_membership() -> None:
    stranger = User.objects.create_user(email="stranger@iteso.mx")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)

    assert IsClanLeader().has_object_permission(_request(stranger), None, clan) is False
