"""Coverage for the /api/v1/actions/, /api/v1/action-categories/ endpoints,
and ActionLogCreateView's points-crediting side effects.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogAuditView, ActionLogCreateView
from green_iteso.clans.services import dissolve_clan
from green_iteso.gamification.models import Badge, UserBadge
from green_iteso.notifications.models import Notification


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
def test_pending_audit_can_be_decided_only_once() -> None:
    user = User.objects.create_user(email="pending@iteso.mx", password="local-only")
    admin = User.objects.create_user(
        email="reviewer@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    clan = Clan.objects.create(name="Audit test clan", type=Clan.ClanType.INSTITUTIONAL)
    profile = UserProfile.objects.create(user=user, institutional_clan=clan)
    category = ActionCategory.objects.create(code="AUDIT", name="Audit")
    action = ActionMaster.objects.create(
        code="PHOTO_AUDIT",
        category=category,
        name="Photo audit",
        description="Evidence requiring review",
        points=10,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key=str(uuid.uuid4()),
        points_awarded=10,
        status=ActionLog.Status.PENDING_AUDIT,
    )
    factory = APIRequestFactory()
    view = ActionLogAuditView.as_view()

    def approve() -> Response:
        request = factory.patch("/api/v1/action-logs/audit/", {"status": "APPROVED"})
        force_authenticate(request, user=admin)
        return view(request, log_id=str(log.pk))

    for payload in ({"status": "UNKNOWN"}, {"status": "REJECTED"}, {}):
        invalid_request = factory.patch(
            "/api/v1/action-logs/audit/", payload, format="json"
        )
        force_authenticate(invalid_request, user=admin)
        assert view(invalid_request, log_id=str(log.pk)).status_code == 400
        log.refresh_from_db()
        assert log.status == ActionLog.Status.PENDING_AUDIT
        assert log.reviewed_at is None

    assert approve().status_code == 200
    assert approve().status_code == 404
    log.refresh_from_db()
    profile.refresh_from_db()
    assert log.reviewed_at is not None
    assert log.reviewed_by_id == admin.pk
    assert profile.total_points == profile.available_points == 10


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


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ("create", "approve"))
def test_action_crossing_points_threshold_awards_badge(operation: str) -> None:
    """Badge eligibility must use the credited balance on both approval paths."""
    user = User.objects.create_user(email="threshold@iteso.mx", password="local-only")
    admin = User.objects.create_user(
        email="threshold-reviewer@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    clan = Clan.objects.create(name="Badge clan", type=Clan.ClanType.INSTITUTIONAL)
    profile = UserProfile.objects.create(
        user=user, institutional_clan=clan, total_points=90, available_points=90
    )
    category = ActionCategory.objects.create(code="BADGE", name="Badge")
    action = ActionMaster.objects.create(
        code="BADGE_THRESHOLD",
        category=category,
        name="Threshold action",
        points=10,
        validation_type=(
            ActionMaster.ValidationType.PHOTO
            if operation == "approve"
            else ActionMaster.ValidationType.NONE
        ),
    )
    badge = Badge.objects.create(name="100 points", points_required=100)
    factory = APIRequestFactory()
    if operation == "create":
        request = factory.post(
            "/api/v1/action-logs/",
            {"action_id": str(action.pk), "idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(request, user=user)
        response = ActionLogCreateView.as_view()(request)
        assert response.status_code == 201
    else:
        log = ActionLog.objects.create(
            user=user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=10,
            status=ActionLog.Status.PENDING_AUDIT,
        )
        request = factory.patch(
            "/api/v1/action-logs/audit/", {"status": "APPROVED"}, format="json"
        )
        force_authenticate(request, user=admin)
        response = ActionLogAuditView.as_view()(request, log_id=str(log.pk))
        assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.total_points == profile.available_points == 100
    assert UserBadge.objects.filter(user=user, badge=badge).count() == 1
    assert Notification.objects.filter(
        user=user, notification_type=Notification.NotificationType.BADGE_EARNED
    ).count() == 1
