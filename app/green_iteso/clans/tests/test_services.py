"""Coverage for clans services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER,
    AlreadyLeadingAClanError,
    DuplicateClanNameError,
    PrivateClanLimitExceededError,
    assign_institutional_clan,
    create_private_clan,
)

from .conftest import join_user_to_new_private_clans


@pytest.mark.django_db
def test_create_private_clan_sets_owner_and_forces_private_type() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_private_clan(name="Green Team", created_by=owner)

    assert clan.created_by == owner
    assert clan.type == Clan.ClanType.PRIVATE
    assert Clan.objects.filter(pk=clan.pk).exists()


@pytest.mark.django_db
def test_create_private_clan_grants_creator_leader_membership() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_private_clan(name="Green Team", created_by=owner)

    membership = ClanMembership.objects.get(user=owner, clan=clan)
    assert membership.role == ClanMembership.MembershipRole.LEADER


@pytest.mark.django_db
def test_create_private_clan_defaults_to_public_privacy() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_private_clan(name="Green Team", created_by=owner)

    assert clan.privacy == Clan.Privacy.PUBLIC


@pytest.mark.django_db
def test_create_private_clan_accepts_private_invite_privacy_and_avatar() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")

    clan = create_private_clan(
        name="Green Team",
        created_by=owner,
        description="Solo por invitación",
        avatar_object_key="clans/avatars/green-team.png",
        privacy=Clan.Privacy.PRIVATE_INVITE,
    )

    assert clan.privacy == Clan.Privacy.PRIVATE_INVITE
    assert clan.avatar_object_key == "clans/avatars/green-team.png"
    assert clan.description == "Solo por invitación"


@pytest.mark.django_db
def test_create_private_clan_rejects_a_duplicate_name() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    other = User.objects.create_user(email="other@iteso.mx", password="local-only")
    create_private_clan(name="Green Team", created_by=owner)

    with pytest.raises(DuplicateClanNameError):
        create_private_clan(name="Green Team", created_by=other)


@pytest.mark.django_db
def test_create_private_clan_rejects_a_second_leadership_for_the_same_user() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    create_private_clan(name="First Clan", created_by=owner)

    with pytest.raises(AlreadyLeadingAClanError):
        create_private_clan(name="Second Clan", created_by=owner)


@pytest.mark.django_db
def test_create_private_clan_rejects_a_sixth_private_clan_membership() -> None:
    member = User.objects.create_user(email="member@iteso.mx", password="local-only")
    join_user_to_new_private_clans(member, MAX_PRIVATE_CLAN_MEMBERSHIPS_PER_USER)

    with pytest.raises(PrivateClanLimitExceededError):
        create_private_clan(name="One Too Many", created_by=member)


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
