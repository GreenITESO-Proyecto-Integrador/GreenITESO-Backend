"""Coverage for accounts selectors."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.selectors import get_profile_clans, get_user_by_id


@pytest.mark.django_db
def test_get_user_by_id_returns_matching_account() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    found = get_user_by_id(user.pk)

    assert found == user


@pytest.fixture(name="member")
def member_fixture() -> User:
    """User with an institutional clan on their profile."""
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=institutional)
    return user


@pytest.mark.django_db
def test_get_profile_clans_reads_the_institutional_clan_from_the_profile(
    member: User,
) -> None:
    result = get_profile_clans(member)

    assert result.profile == member.profile
    assert result.institutional_clan == member.profile.institutional_clan
    assert result.active_private_clan is None


@pytest.mark.django_db
def test_get_profile_clans_reads_the_active_private_clan_from_the_membership(
    member: User,
) -> None:
    active = Clan.objects.create(name="Las Ranas", type=Clan.ClanType.PRIVATE)
    inactive = Clan.objects.create(name="Eco Warriors", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=member, clan=active, is_active_private=True)
    ClanMembership.objects.create(user=member, clan=inactive)

    result = get_profile_clans(member)

    assert result.active_private_clan == active
    assert result.institutional_clan == member.profile.institutional_clan


@pytest.mark.django_db
def test_get_profile_clans_ignores_another_users_active_membership(
    member: User,
) -> None:
    other = User.objects.create_user(email="luis@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Las Ranas", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=other, clan=clan, is_active_private=True)

    assert get_profile_clans(member).active_private_clan is None


@pytest.mark.django_db
def test_get_profile_clans_raises_when_the_account_has_no_profile() -> None:
    user = User.objects.create_user(email="sin-perfil@iteso.mx", password="local-only")

    with pytest.raises(UserProfile.DoesNotExist):
        get_profile_clans(user)
