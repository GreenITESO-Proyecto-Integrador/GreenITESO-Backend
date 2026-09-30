"""Coverage for accounts model-level behavior (managers, field defaults)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, Friendship, User, UserProfile


@pytest.mark.django_db
def test_userprofile_visibility_defaults_to_public() -> None:
    user = User.objects.create_user(email="profile@iteso.mx")

    profile = UserProfile.objects.create(user=user)

    assert profile.visibility == UserProfile.Visibility.PUBLIC


@pytest.mark.django_db
def test_user_nickname_defaults_to_blank() -> None:
    user = User.objects.create_user(email="nickname@iteso.mx")

    assert user.nickname == ""


@pytest.mark.django_db
def test_userprofile_available_points_defaults_to_zero() -> None:
    user = User.objects.create_user(email="points@iteso.mx")

    profile = UserProfile.objects.create(user=user)

    assert profile.available_points == 0


@pytest.mark.django_db
def test_clan_default_manager_excludes_soft_deleted() -> None:
    alive = Clan.objects.create(name="Alive", type=Clan.ClanType.PRIVATE)
    Clan.objects.create(
        name="Deleted", type=Clan.ClanType.PRIVATE, deleted_at=timezone.now()
    )

    assert list(Clan.objects.all()) == [alive]


@pytest.mark.django_db
def test_clan_all_objects_includes_soft_deleted() -> None:
    Clan.objects.create(name="Alive", type=Clan.ClanType.PRIVATE)
    Clan.objects.create(
        name="Deleted", type=Clan.ClanType.PRIVATE, deleted_at=timezone.now()
    )

    assert Clan.all_objects.count() == 2


@pytest.mark.django_db
def test_clan_name_can_be_reused_after_soft_delete() -> None:
    original = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    original.deleted_at = timezone.now()
    original.save(update_fields=["deleted_at"])

    reused = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)

    assert Clan.all_objects.filter(name="Green Team").count() == 2
    assert reused.deleted_at is None


@pytest.mark.django_db
def test_clan_name_still_unique_among_alive_clans() -> None:
    Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)

    with pytest.raises(IntegrityError), transaction.atomic():
        Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)


@pytest.mark.django_db
def test_friendship_save_derives_the_pair_without_it_being_set_explicitly() -> None:
    """Regression: the admin Add form can't submit low_user/high_user (read-only).

    Without deriving the pair in save(), Friendship.objects.create(requester=,
    addressee=) alone -- the shape the admin's ModelForm produces -- fails
    the NOT NULL columns at the database.
    """
    ana = User.objects.create_user(email="ana@iteso.mx")
    beto = User.objects.create_user(email="beto@iteso.mx")

    friendship = Friendship.objects.create(requester=ana, addressee=beto)

    expected_low, expected_high = sorted([ana.pk, beto.pk])
    assert friendship.low_user == expected_low
    assert friendship.high_user == expected_high
