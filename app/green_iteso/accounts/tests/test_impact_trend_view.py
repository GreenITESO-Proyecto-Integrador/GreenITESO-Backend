"""Coverage for GET /api/v1/profile/me/impact-trend/."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import ActionCategory, ActionLog
from green_iteso.actions.tests.helpers import create_compost_action

IMPACT_TREND_URL = "/api/v1/profile/me/impact-trend/"


def _create_action_log(
    *, user: User, institutional_clan: Clan, created_at: datetime
) -> ActionLog:
    category = ActionCategory.objects.create(code="CAT-TREND", name="Waste")
    action = create_compost_action(code="ACT-TREND", category=category)
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=institutional_clan,
        idempotency_key=f"trend-{created_at.isoformat()}",
        points_awarded=action.points,
        co2_kg_factor_snapshot=Decimal("1.500"),
        water_liters_factor_snapshot=Decimal("2.500"),
        plastic_kg_factor_snapshot=Decimal("0.500"),
        status=ActionLog.Status.APPROVED,
    )
    ActionLog.objects.filter(pk=log.pk).update(created_at=created_at)
    return log


@pytest.mark.django_db
def test_impact_trend_requires_authentication() -> None:
    response = APIClient().get(IMPACT_TREND_URL)

    assert response.status_code == 401


@pytest.mark.django_db
def test_impact_trend_returns_four_weeks_for_a_fresh_user() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(IMPACT_TREND_URL)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 4
    assert all(point["co2_kg"] == "0.000" for point in body)
    assert all(point["water_liters"] == "0.000" for point in body)
    assert all(point["plastic_kg"] == "0.000" for point in body)
    # Oldest week first.
    week_starts = [point["week_start"] for point in body]
    assert week_starts == sorted(week_starts)


@pytest.mark.django_db
def test_impact_trend_sums_approved_actions_into_the_current_week() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    now = timezone.now()
    _create_action_log(
        user=caller, institutional_clan=institutional_clan, created_at=now
    )
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(IMPACT_TREND_URL)

    body = response.json()
    current_week = body[-1]
    assert current_week["co2_kg"] == "1.500"
    assert current_week["water_liters"] == "2.500"
    assert current_week["plastic_kg"] == "0.500"
    assert all(point["co2_kg"] == "0.000" for point in body[:-1])


@pytest.mark.django_db
def test_impact_trend_only_shows_the_callers_own_actions() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    other = User.objects.create_user(email="luis@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    _create_action_log(
        user=other, institutional_clan=institutional_clan, created_at=timezone.now()
    )
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(IMPACT_TREND_URL)

    assert all(point["co2_kg"] == "0.000" for point in response.json())
