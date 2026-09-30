"""Coverage for accounts selectors."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.selectors import get_ecological_profile, get_user_by_id
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant


@pytest.mark.django_db
def test_get_user_by_id_returns_matching_account() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    found = get_user_by_id(user.pk)

    assert found == user


def _create_action_log(
    *, user: User, institutional_clan: Clan, status: str, idempotency_key: str
) -> ActionLog:
    category = ActionCategory.objects.create(
        code=f"CAT-{idempotency_key}", name="Waste"
    )
    action = ActionMaster.objects.create(
        code=f"ACT-{idempotency_key}",
        category=category,
        name="Compost",
        description="Compost organic waste",
        points=15,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=institutional_clan,
        idempotency_key=idempotency_key,
        points_awarded=action.points,
        co2_kg_factor_snapshot=Decimal("1.500"),
        water_liters_factor_snapshot=Decimal("2.500"),
        plastic_kg_factor_snapshot=Decimal("0.500"),
        status=status,
    )


def _create_campaign(*, creator: User, status: str, title: str) -> Campaign:
    now = timezone.now()
    return Campaign.objects.create(
        title=title,
        scope=Campaign.Scope.GLOBAL,
        status=status,
        creator=creator,
        start_date=now,
        end_date=now + timedelta(days=1),
    )


@pytest.mark.django_db
def test_get_ecological_profile_returns_placeholders_for_a_fresh_user() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    profile = get_ecological_profile(user)

    assert profile.user == user
    assert profile.level is None
    assert not profile.badges
    assert profile.active_private_clan is None
    assert profile.impact_metrics.co2_kg == Decimal("0")
    assert profile.impact_metrics.water_liters == Decimal("0")
    assert profile.impact_metrics.plastic_kg == Decimal("0")
    assert not profile.finished_campaigns
    # ensure_profile creates the row on first access, matching accounts.services.
    assert UserProfile.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_get_ecological_profile_sums_impact_from_approved_actions_only() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="a1",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="a2",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.REJECTED,
        idempotency_key="a3",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.PENDING_AUDIT,
        idempotency_key="a4",
    )

    profile = get_ecological_profile(user)

    assert profile.impact_metrics.co2_kg == Decimal("3.000")
    assert profile.impact_metrics.water_liters == Decimal("5.000")
    assert profile.impact_metrics.plastic_kg == Decimal("1.000")


@pytest.mark.django_db
def test_get_ecological_profile_includes_only_finished_campaigns() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    finished = _create_campaign(
        creator=user, status=Campaign.Status.FINISHED, title="Semana Verde"
    )
    in_progress = _create_campaign(
        creator=user, status=Campaign.Status.IN_PROGRESS, title="Reto Reciclaje"
    )
    CampaignParticipant.objects.create(campaign=finished, user=user)
    CampaignParticipant.objects.create(campaign=in_progress, user=user)

    profile = get_ecological_profile(user)

    assert [campaign.title for campaign in profile.finished_campaigns] == [
        "Semana Verde"
    ]


@pytest.mark.django_db
def test_get_ecological_profile_includes_the_active_private_clan() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=user, clan=clan, is_active_private=True)

    profile = get_ecological_profile(user)

    assert profile.active_private_clan == clan
