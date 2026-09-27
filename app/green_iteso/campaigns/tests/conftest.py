"""Fixtures shared by the campaigns tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant, Mission
from green_iteso.campaigns.tests.helpers import campaign_data


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="student@example.test", password="test-pass")


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user(email="other@example.test", password="test-pass")


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_user(
        email="admin@example.test", password="test-pass", role=User.Role.ADMIN
    )


@pytest.fixture
def clan(user: User) -> Clan:
    return Clan.objects.create(
        name="Test clan",
        type=Clan.ClanType.PRIVATE,
        created_by=user,
    )


@pytest.fixture
def action_category() -> ActionCategory:
    return ActionCategory.objects.create(
        code="WASTE",
        name="Waste",
        description="Waste actions",
    )


@pytest.fixture
def action(action_category: ActionCategory) -> ActionMaster:
    return ActionMaster.objects.create(
        code="RECYCLE",
        category=action_category,
        name="Recycle",
        description="Recycle something",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )


@pytest.fixture
def campaign_factory(user: User) -> Callable[..., Campaign]:
    def create_campaign(**overrides: Any) -> Campaign:
        values = campaign_data(creator=user, **overrides)
        return Campaign.objects.create(**values)

    return create_campaign


@pytest.fixture
def campaign(campaign_factory: Any) -> Campaign:
    return campaign_factory()


@pytest.fixture
def mission(campaign: Campaign, action: ActionMaster, user: User) -> Mission:
    mission = Mission.objects.create(campaign=campaign, action=action, target_count=5)
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    return mission


@pytest.fixture
def second_action(action_category: ActionCategory) -> ActionMaster:
    return ActionMaster.objects.create(
        code="COMPOST",
        category=action_category,
        name="Compost",
        description="Compost something",
        points=5,
        validation_type=ActionMaster.ValidationType.NONE,
    )


@pytest.fixture
def private_campaign(user: User, clan: Clan, campaign_factory: Any) -> Campaign:
    return campaign_factory(scope=Campaign.Scope.PRIVATE, target_clan=clan)
