"""Coverage for accounts services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import User, UserProfile
from green_iteso.accounts.services import ensure_profile


@pytest.mark.django_db
def test_ensure_profile_creates_once() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    first = ensure_profile(user)
    second = ensure_profile(user)

    assert first == second
    assert UserProfile.objects.filter(user=user).count() == 1
