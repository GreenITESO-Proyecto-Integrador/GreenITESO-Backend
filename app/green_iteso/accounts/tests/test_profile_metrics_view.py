# pylint: disable=redefined-outer-name
"""Coverage for GET /api/v1/profile/me/metrics/ (#126)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant
from green_iteso.gamification.models import Badge, UserBadge

METRICS_URL = "/api/v1/profile/me/metrics/"

LogAction = Callable[..., ActionLog]


@pytest.fixture
def caller() -> User:
    return User.objects.create_user(email="ana@iteso.mx", password="local-only")


@pytest.fixture
def client(caller: User) -> APIClient:
    api_client = APIClient()
    api_client.force_authenticate(caller)
    return api_client


@pytest.fixture
def waste() -> ActionCategory:
    return ActionCategory.objects.create(code="waste", name="Residuos")


@pytest.fixture
def water() -> ActionCategory:
    return ActionCategory.objects.create(code="water", name="Agua")


@pytest.fixture
def log_action() -> LogAction:
    """Build an action log in ``category`` created at ``created_at`` (UTC)."""
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    sequence = iter(range(1000))

    def build(
        user: User,
        category: ActionCategory,
        created_at: datetime,
        *,
        status: str = ActionLog.Status.APPROVED,
        points: int = 10,
    ) -> ActionLog:
        key = f"log-{next(sequence)}"
        action = ActionMaster.objects.create(
            code=key,
            category=category,
            name=f"Acción {key}",
            description="Synthetic metrics action",
            points=points,
            validation_type=ActionMaster.ValidationType.NONE,
        )
        log = ActionLog.objects.create(
            user=user,
            action=action,
            institutional_clan=institutional_clan,
            idempotency_key=key,
            points_awarded=points,
            co2_kg_factor_snapshot=Decimal("1.250"),
            water_liters_factor_snapshot=Decimal("3.000"),
            plastic_kg_factor_snapshot=Decimal("0.100"),
            status=status,
        )
        # ``created_at`` is auto_now_add, so the timestamp is set afterwards.
        ActionLog.objects.filter(pk=log.pk).update(created_at=created_at)
        return log

    return build


def _utc(year: int, month: int, day: int, hour: int = 18) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.mark.django_db
def test_metrics_require_authentication() -> None:
    response = APIClient().get(METRICS_URL)

    assert response.status_code == 401


@pytest.mark.django_db
def test_metrics_report_zeros_for_a_user_without_activity(client: APIClient) -> None:
    response = client.get(METRICS_URL)

    assert response.status_code == 200
    assert response.json() == {
        "filters": {"from": None, "to": None, "category": None, "granularity": "week"},
        "impact": {"co2_kg": "0.000", "water_liters": "0.000", "plastic_kg": "0.000"},
        "actions": {"approved": 0, "points_earned": 0, "by_category": []},
        "points": {"total": 0, "available": 0},
        "activity": [],
        "achievements": {"badges_earned": 0, "finished_campaigns": 0},
    }


@pytest.mark.django_db
def test_metrics_count_only_the_callers_approved_actions(
    caller: User,
    client: APIClient,
    log_action: LogAction,
    waste: ActionCategory,
    water: ActionCategory,
) -> None:
    other = User.objects.create_user(email="otro@iteso.mx", password="local-only")
    log_action(caller, waste, _utc(2026, 10, 5), points=10)
    log_action(caller, waste, _utc(2026, 10, 6), points=20)
    log_action(caller, water, _utc(2026, 10, 6), points=5)
    log_action(caller, water, _utc(2026, 10, 7), status=ActionLog.Status.REJECTED)
    log_action(caller, water, _utc(2026, 10, 7), status=ActionLog.Status.PENDING_AUDIT)
    log_action(other, waste, _utc(2026, 10, 7), points=50)
    UserProfile.objects.create(user=caller, total_points=35, available_points=12)

    body = client.get(METRICS_URL).json()

    assert body["impact"] == {
        "co2_kg": "3.750",
        "water_liters": "9.000",
        "plastic_kg": "0.300",
    }
    assert body["actions"] == {
        "approved": 3,
        "points_earned": 35,
        "by_category": [
            {"code": "waste", "name": "Residuos", "approved_actions": 2, "points": 30},
            {"code": "water", "name": "Agua", "approved_actions": 1, "points": 5},
        ],
    }
    assert body["points"] == {"total": 35, "available": 12}


@pytest.mark.django_db
def test_metrics_group_activity_by_monday_week_and_by_month(
    caller: User, client: APIClient, log_action: LogAction, waste: ActionCategory
) -> None:
    log_action(caller, waste, _utc(2026, 9, 30), points=10)  # Wed, week of Sep 28
    log_action(caller, waste, _utc(2026, 10, 4), points=20)  # Sun, week of Sep 28
    log_action(caller, waste, _utc(2026, 10, 5), points=5)  # Mon, week of Oct 5

    weekly = client.get(METRICS_URL).json()["activity"]
    monthly = client.get(METRICS_URL, {"granularity": "month"}).json()["activity"]

    assert weekly == [
        {"period_start": "2026-09-28", "approved_actions": 2, "points": 30},
        {"period_start": "2026-10-05", "approved_actions": 1, "points": 5},
    ]
    assert monthly == [
        {"period_start": "2026-09-01", "approved_actions": 1, "points": 10},
        {"period_start": "2026-10-01", "approved_actions": 2, "points": 25},
    ]


@pytest.mark.django_db
def test_metrics_date_range_uses_mexico_city_calendar_days(
    caller: User, client: APIClient, log_action: LogAction, waste: ActionCategory
) -> None:
    # 05:30 UTC on Oct 5 is 23:30 on Oct 4 in Mexico City (UTC-6).
    log_action(caller, waste, _utc(2026, 10, 5, hour=5).replace(minute=30))
    log_action(caller, waste, _utc(2026, 10, 6))

    until_oct_4 = client.get(METRICS_URL, {"to": "2026-10-04"}).json()
    from_oct_5 = client.get(METRICS_URL, {"from": "2026-10-05"}).json()
    single_day = client.get(
        METRICS_URL, {"from": "2026-10-06", "to": "2026-10-06"}
    ).json()

    assert until_oct_4["actions"]["approved"] == 1
    assert until_oct_4["activity"] == [
        {"period_start": "2026-09-28", "approved_actions": 1, "points": 10}
    ]
    assert from_oct_5["actions"]["approved"] == 1
    assert single_day["actions"]["approved"] == 1
    assert single_day["filters"]["from"] == "2026-10-06"
    assert single_day["filters"]["to"] == "2026-10-06"


@pytest.mark.django_db
def test_metrics_category_narrows_only_action_metrics(
    caller: User,
    client: APIClient,
    log_action: LogAction,
    waste: ActionCategory,
    water: ActionCategory,
) -> None:
    log_action(caller, waste, _utc(2026, 10, 5), points=10)
    log_action(caller, water, _utc(2026, 10, 5), points=5)
    badge = Badge.objects.create(name="Primer paso", description="Primera acción")
    UserBadge.objects.create(user=caller, badge=badge)
    UserProfile.objects.create(user=caller, total_points=15)

    body = client.get(METRICS_URL, {"category": "water"}).json()

    assert body["filters"]["category"] == "water"
    assert body["actions"]["approved"] == 1
    assert body["actions"]["points_earned"] == 5
    assert body["impact"]["co2_kg"] == "1.250"
    assert [row["code"] for row in body["actions"]["by_category"]] == ["water"]
    assert body["points"]["total"] == 15
    assert body["achievements"]["badges_earned"] == 1


@pytest.mark.django_db
def test_metrics_count_badges_and_finished_campaigns_within_the_range(
    caller: User, client: APIClient
) -> None:
    first = Badge.objects.create(name="Primer paso", description="Primera acción")
    second = Badge.objects.create(name="Constante", description="Una semana")
    UserBadge.objects.create(user=caller, badge=first)
    late = UserBadge.objects.create(user=caller, badge=second)
    UserBadge.objects.filter(pk=late.pk).update(earned_at=_utc(2026, 10, 20))
    UserBadge.objects.exclude(pk=late.pk).update(earned_at=_utc(2026, 10, 2))
    for title, status, end in (
        ("Semana Verde", Campaign.Status.FINISHED, _utc(2026, 10, 3)),
        ("Reto Reciclaje", Campaign.Status.FINISHED, _utc(2026, 10, 25)),
        ("Reto Agua", Campaign.Status.IN_PROGRESS, _utc(2026, 10, 3)),
    ):
        campaign = Campaign.objects.create(
            title=title,
            scope=Campaign.Scope.GLOBAL,
            status=status,
            creator=caller,
            start_date=end - timedelta(days=7),
            end_date=end,
        )
        CampaignParticipant.objects.create(campaign=campaign, user=caller)

    everything = client.get(METRICS_URL).json()
    early_october = client.get(
        METRICS_URL, {"from": "2026-10-01", "to": "2026-10-10"}
    ).json()

    assert everything["achievements"] == {"badges_earned": 2, "finished_campaigns": 2}
    assert early_october["achievements"] == {
        "badges_earned": 1,
        "finished_campaigns": 1,
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"from": "2026-13-01"}, "from"),
        ({"to": "ayer"}, "to"),
        ({"from": "2026-10-10", "to": "2026-10-01"}, "from"),
        ({"category": "does-not-exist"}, "category"),
        ({"granularity": "day"}, "granularity"),
    ],
)
def test_metrics_reject_invalid_query_params(
    client: APIClient, params: dict[str, str], field: str
) -> None:
    response = client.get(METRICS_URL, params)

    assert response.status_code == 400
    assert field in response.json()
