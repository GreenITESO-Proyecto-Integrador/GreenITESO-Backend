# pylint: disable=redefined-outer-name
"""Shared fixtures for the clans domain tests.

Fixtures composing other fixtures (e.g. ``clan(leader)``) is the standard
pytest dependency-injection pattern, not an actual shadowing bug.
"""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, User
from green_iteso.clans.services import create_clan


@pytest.fixture
def leader() -> User:
    """A user who owns and leads a private clan."""
    return User.objects.create_user(email="lead@iteso.mx", password="local-only")


@pytest.fixture
def member() -> User:
    """A second user, not a member of any clan by default."""
    return User.objects.create_user(email="member@iteso.mx", password="local-only")


@pytest.fixture
def clan(leader: User) -> Clan:
    """A private clan created and led by ``leader``."""
    return create_clan(
        name="Green Team", clan_type=Clan.ClanType.PRIVATE, created_by=leader
    )
