"""Coverage for accounts model-level behavior (managers, field defaults)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, User, UserProfile


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
