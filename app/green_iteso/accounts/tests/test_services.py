"""Coverage for accounts services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import User, UserProfile
from green_iteso.accounts.services import ensure_profile, update_profile


@pytest.mark.django_db
def test_ensure_profile_creates_once() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    first = ensure_profile(user)
    second = ensure_profile(user)

    assert first == second
    assert UserProfile.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_update_profile_applies_only_the_provided_fields() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    first = update_profile(user=user, bio="Loves recycling.")
    second = update_profile(user=user, visibility=UserProfile.Visibility.PRIVATE)

    assert second.bio == "Loves recycling."
    assert second.visibility == UserProfile.Visibility.PRIVATE
    assert first.pk == second.pk


@pytest.mark.django_db
def test_update_profile_sets_preferences_and_avatar_url() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    profile = update_profile(
        user=user,
        preferences={"theme": "dark"},
        avatar_url="https://storage.googleapis.com/bucket/ana.png",
    )

    assert profile.preferences == {"theme": "dark"}
    assert profile.avatar_url == "https://storage.googleapis.com/bucket/ana.png"


@pytest.mark.django_db
def test_update_profile_can_clear_bio_and_avatar_url_with_empty_values() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    update_profile(
        user=user, bio="Old bio", avatar_url="https://storage.googleapis.com/a.png"
    )

    cleared = update_profile(user=user, bio="", avatar_url="")

    assert cleared.bio == ""
    assert cleared.avatar_url == ""


@pytest.mark.django_db
def test_update_profile_leaves_unspecified_fields_untouched() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    update_profile(user=user, bio="Keep me")

    unchanged = update_profile(user=user, visibility=UserProfile.Visibility.PRIVATE)

    assert unchanged.bio == "Keep me"
