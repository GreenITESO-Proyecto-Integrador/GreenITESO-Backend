"""Coverage for accounts selectors."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import User
from green_iteso.accounts.selectors import get_user_by_id


@pytest.mark.django_db
def test_get_user_by_id_returns_matching_account() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    found = get_user_by_id(user.pk)

    assert found == user
