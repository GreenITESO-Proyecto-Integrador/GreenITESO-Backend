"""Coverage for the /api/v1/actions/, /api/v1/action-categories/ endpoints,
and ActionLogCreateView's points-crediting side effects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogCreateView


@pytest.mark.django_db
def test_list_action_categories_requires_authentication() -> None:
    response = APIClient().get("/api/v1/action-categories/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_actions_requires_authentication() -> None:
    response = APIClient().get("/api/v1/actions/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_actions_and_categories_for_authenticated_caller() -> None:
    caller = User.objects.create_user(email="user@iteso.mx", password="local-only")
    category = ActionCategory.objects.create(
        code="RECYCLE",
        name="Recycling",
        description="Recycling plastic and paper",
    )
    ActionMaster.objects.create(
        code="RECYCLE_PLASTIC",
        category=category,
        name="Recycle Plastic Bottle",
        description="Recycle a PET bottle",
        points=10,
        daily_limit=3,
        validation_type=ActionMaster.ValidationType.NONE,
    )

    client = APIClient()
    client.force_authenticate(caller)

    cat_response = client.get("/api/v1/action-categories/")
    assert cat_response.status_code == 200
    assert len(cat_response.json()["results"]) == 1
    assert cat_response.json()["results"][0]["code"] == "RECYCLE"

    action_response = client.get("/api/v1/actions/")
    assert action_response.status_code == 200
    assert len(action_response.json()["results"]) == 1
    assert action_response.json()["results"][0]["code"] == "RECYCLE_PLASTIC"


@pytest.mark.django_db
def test_approved_action_credits_available_points_alongside_total_points() -> None:
    """Regression test: available_points must accrue, not stay stuck at 0.

    UserProfile.available_points (T2-02) is the spendable balance; it is
    documented to rise together with total_points and only total_points is
    drawn down later by the redemption flow, so a POST here must credit both.
    """
    user = User.objects.create_user(email="student@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=institutional_clan)
    category = ActionCategory.objects.create(code="WASTE", name="Waste")
    action = ActionMaster.objects.create(
        code="RECYCLE",
        category=category,
        name="Recycle",
        description="Recycle something",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )

    request = APIRequestFactory().post(
        "/api/v1/actions/logs/",
        {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )
    force_authenticate(request, user=user)

    response = ActionLogCreateView.as_view()(request)

    assert response.status_code == 201
    user.profile.refresh_from_db()
    assert user.profile.total_points == 10
    assert user.profile.available_points == 10


@pytest.mark.django_db
@pytest.mark.parametrize(
    "prior_status",
    [
        ActionLog.Status.APPROVED,
        ActionLog.Status.PENDING_AUDIT,
        ActionLog.Status.REJECTED,
    ],
)
def test_action_daily_limit_rejects_another_submission_on_same_local_day(
    prior_status: str,
) -> None:
    user = User.objects.create_user(email="daily@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Ingeniería", type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    category = ActionCategory.objects.create(code="DAILY", name="Daily")
    action = ActionMaster.objects.create(
        code="DAILY_ACTION",
        category=category,
        name="Daily action",
        description="One per local day",
        points=10,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="first",
        points_awarded=action.points,
        status=prior_status,
    )
    request = APIRequestFactory().post(
        "/api/v1/actions/logs/",
        {"action_id": str(action.id), "idempotency_key": "second"},
        format="json",
    )
    force_authenticate(request, user=user)

    response = ActionLogCreateView.as_view()(request)

    assert response.status_code == 429
    assert ActionLog.objects.filter(user=user, action=action).count() == 1


@pytest.mark.django_db
def test_action_daily_limit_resets_at_mexico_city_midnight() -> None:
    user = User.objects.create_user(email="midnight@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Campus", type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    category = ActionCategory.objects.create(code="MIDNIGHT", name="Midnight")
    action = ActionMaster.objects.create(
        code="MIDNIGHT_ACTION",
        category=category,
        name="Midnight action",
        description="Resets at local midnight",
        points=10,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    previous_day_log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="previous-day",
        points_awarded=action.points,
    )
    ActionLog.objects.filter(pk=previous_day_log.pk).update(
        created_at=datetime(2026, 9, 25, 5, 59, tzinfo=UTC)
    )
    request = APIRequestFactory().post(
        "/api/v1/actions/logs/",
        {"action_id": str(action.id), "idempotency_key": "new-day"},
        format="json",
    )
    force_authenticate(request, user=user)

    with patch(
        "django.utils.timezone.now",
        return_value=datetime(2026, 9, 25, 6, 0, tzinfo=UTC),
    ):
        response = ActionLogCreateView.as_view()(request)

    assert response.status_code == 201
    assert ActionLog.objects.filter(user=user, action=action).count() == 2
