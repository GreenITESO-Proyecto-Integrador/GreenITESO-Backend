"""Coverage for the /api/v1/actions/, /api/v1/action-categories/ endpoints,
and ActionLogCreateView's points-crediting side effects.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogAuditView, ActionLogCreateView
from green_iteso.clans.services import dissolve_clan


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
def test_action_credits_snapshot_clan_if_it_is_dissolved_during_submission() -> None:
    """An ActionLog snapshot must agree with the dissolved clan's retained points."""
    user = User.objects.create_user(email="leader@iteso.mx", password="local-only")
    private_clan = Clan.objects.create(
        name="Test private clan", type=Clan.ClanType.PRIVATE, created_by=user
    )
    institutional_clan = Clan.objects.create(
        name="Test institutional clan", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=institutional_clan)
    ClanMembership.objects.create(
        user=user,
        clan=private_clan,
        role=ClanMembership.MembershipRole.LEADER,
        is_active_private=True,
    )
    category = ActionCategory.objects.create(code="WASTE", name="Waste")
    action = ActionMaster.objects.create(
        code="RECYCLE",
        category=category,
        name="Recycle",
        description="Recycle something",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )

    original_create = ActionLog.objects.create

    def create_after_dissolve(**kwargs: object) -> ActionLog:
        dissolve_clan(clan=private_clan, actor=user)
        return original_create(**kwargs)

    request = APIRequestFactory().post(
        "/api/v1/actions/logs/",
        {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )
    force_authenticate(request, user=user)

    with patch.object(ActionLog.objects, "create", side_effect=create_after_dissolve):
        response = ActionLogCreateView.as_view()(request)

    assert response.status_code == 201
    action_log = ActionLog.objects.get(pk=response.data["log_id"])
    assert action_log.credited_private_clan_id == private_clan.pk
    private_clan.refresh_from_db()
    assert private_clan.deleted_at is not None
    assert private_clan.total_points == action.points


@pytest.mark.django_db
def test_action_credits_institutional_clan_if_soft_deleted_during_submission() -> None:
    user = User.objects.create_user(
        email="institutional@iteso.mx", password="local-only"
    )
    clan = Clan.objects.create(name="Institutional", type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    category = ActionCategory.objects.create(code="WASTE", name="Waste")
    action = ActionMaster.objects.create(
        code="RECYCLE",
        category=category,
        name="Recycle",
        description="Recycle something",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    original_create = ActionLog.objects.create

    def create_after_dissolve(**kwargs: object) -> ActionLog:
        Clan.all_objects.filter(pk=clan.pk).update(deleted_at=timezone.now())
        return original_create(**kwargs)

    request = APIRequestFactory().post(
        "/api/v1/actions/logs/",
        {"action_id": str(action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )
    force_authenticate(request, user=user)
    with patch.object(ActionLog.objects, "create", side_effect=create_after_dissolve):
        response = ActionLogCreateView.as_view()(request)

    assert response.status_code == 201
    clan.refresh_from_db()
    assert clan.deleted_at is not None
    assert clan.total_points == action.points


@pytest.mark.django_db(transaction=True)
def test_concurrent_audits_credit_a_pending_log_only_once() -> None:
    """Two admins racing on one pending log must not grant points twice."""
    admin = User.objects.create_superuser(email="admin@iteso.mx", password="local-only")
    student = User.objects.create_user(email="student@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Institutional", type=Clan.ClanType.INSTITUTIONAL)
    profile = UserProfile.objects.create(user=student, institutional_clan=clan)
    category = ActionCategory.objects.create(code="WASTE", name="Waste")
    action = ActionMaster.objects.create(
        code="PHOTO",
        category=category,
        name="Photo",
        description="Evidence",
        points=10,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )
    log = ActionLog.objects.create(
        user=student,
        action=action,
        institutional_clan=clan,
        idempotency_key="one-pending-log",
        points_awarded=10,
        status=ActionLog.Status.PENDING_AUDIT,
    )
    barrier = Barrier(2)
    original_get = ActionLog.objects.get

    def get_pending(*args: object, **kwargs: object) -> ActionLog:
        result = original_get(*args, **kwargs)
        if kwargs.get("status") == ActionLog.Status.PENDING_AUDIT:
            barrier.wait(timeout=10)
        return result

    def approve(_: int) -> int:
        close_old_connections()
        try:
            request = APIRequestFactory().patch(
                f"/api/v1/action-logs/{log.pk}/audit/",
                {"status": "APPROVED"},
                format="json",
            )
            force_authenticate(request, user=admin)
            return ActionLogAuditView.as_view()(request, log_id=log.pk).status_code
        finally:
            close_old_connections()

    with patch.object(ActionLog.objects, "get", side_effect=get_pending):
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(approve, range(2)))

    assert sorted(statuses) == [200, 404]
    log.refresh_from_db()
    assert log.reviewed_at is not None
    profile.refresh_from_db()
    clan.refresh_from_db()
    assert profile.total_points == profile.available_points == 10
    assert clan.total_points == 10
