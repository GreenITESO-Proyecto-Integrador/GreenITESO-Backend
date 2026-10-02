"""Coverage for the /api/v1/actions/, /api/v1/action-categories/ endpoints,
and ActionLogCreateView's points-crediting side effects.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from typing import Any
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogAuditView, ActionLogCreateView
from green_iteso.clans.services import dissolve_clan
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
    # pylint: disable=too-many-locals,too-many-statements
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
    first_selected = Event()
    second_query = Event()
    second_finished = Event()
    second_blocked = Event()
    competitor_pid: list[int] = []
    observed_queries: list[bool] = []

    def approve(first: bool) -> int:
        close_old_connections()
        try:
            if not first:
                assert first_selected.wait(timeout=5)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '8s'")
                actor = User.objects.get(pk=admin.pk)

                def synchronize(
                    execute: Callable[..., Any],
                    sql: str,
                    params: Any,
                    many: bool,
                    context: Any,
                ) -> Any:
                    if 'FROM "actions_action_log"' not in sql or log.pk not in (
                        params or ()
                    ):
                        return execute(sql, params, many, context)
                    observed_queries.append(first)
                    if not first:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_backend_pid()")
                            competitor_pid.append(cursor.fetchone()[0])
                        second_query.set()
                        return execute(sql, params, many, context)
                    result = execute(sql, params, many, context)
                    first_selected.set()
                    assert second_query.wait(timeout=5)
                    peer_deadline = monotonic() + 5
                    while monotonic() < peer_deadline:
                        with connection.cursor() as monitor:
                            monitor.execute("SELECT pg_stat_clear_snapshot()")
                            monitor.execute(
                                "SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE pid = %s AND datname = current_database()",
                                [competitor_pid[0]],
                            )
                            if monitor.fetchone() == ("Lock",):
                                second_blocked.set()
                                break
                        # Negative control: an unlocked lookup lets the peer
                        # commit first, so the buffered pending decision can
                        # credit twice instead of merely timing out here.
                        if second_finished.is_set():
                            break
                        sleep(0.01)
                    else:
                        raise AssertionError(
                            "Competing audit neither blocked nor finished"
                        )
                    return result

                with connection.execute_wrapper(synchronize):
                    request = APIRequestFactory().patch(
                        f"/api/v1/action-logs/{log.pk}/audit/",
                        {"status": "APPROVED"},
                        format="json",
                    )
                    force_authenticate(request, user=actor)
                    return ActionLogAuditView.as_view()(
                        request, log_id=log.pk
                    ).status_code
        finally:
            if not first:
                second_finished.set()
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(approve, True)
        second = executor.submit(approve, False)
        statuses = [first.result(timeout=15), second.result(timeout=15)]

    assert observed_queries == [True, False]
    log.refresh_from_db()
    assert log.reviewed_at is not None
    profile.refresh_from_db()
    clan.refresh_from_db()
    assert (
        sorted(statuses),
        profile.total_points,
        profile.available_points,
        clan.total_points,
        second_blocked.is_set(),
    ) == ([200, 404], 10, 10, 10, True)


@pytest.mark.django_db
@pytest.mark.parametrize("has_profile", (True, False))
def test_audit_credits_frozen_deleted_clans_or_rolls_back_missing_profile(
    has_profile: bool,
) -> None:
    """Missing profiles still abort; dissolved snapshots retain awarded credits."""
    user = User.objects.create_user(email="frozen-credit@iteso.mx")
    admin = User.objects.create_user(
        email="frozen-admin@iteso.mx", role=User.Role.ADMIN
    )
    original = Clan.objects.create(
        name="Frozen institutional", type=Clan.ClanType.INSTITUTIONAL
    )
    private = Clan.objects.create(name="Frozen private", type=Clan.ClanType.PRIVATE)
    current = Clan.objects.create(
        name="Current institutional", type=Clan.ClanType.INSTITUTIONAL
    )
    if has_profile:
        UserProfile.objects.create(user=user, institutional_clan=current)
    category = ActionCategory.objects.create(code="FROZEN", name="Frozen")
    action = ActionMaster.objects.create(
        code="FROZEN",
        category=category,
        name="Frozen",
        points=99,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=original,
        credited_private_clan=private,
        idempotency_key=str(uuid.uuid4()),
        points_awarded=10,
        status=ActionLog.Status.PENDING_AUDIT,
    )
    Clan.all_objects.filter(pk__in=[original.pk, private.pk]).update(
        deleted_at=timezone.now()
    )
    request = APIRequestFactory().patch(
        "/api/v1/action-logs/audit/", {"status": "APPROVED"}, format="json"
    )
    force_authenticate(request, user=admin)
    view = ActionLogAuditView.as_view()
    if has_profile:
        assert view(request, log_id=str(log.pk)).status_code == 200
    else:
        with pytest.raises(UserProfile.DoesNotExist):
            view(request, log_id=str(log.pk))
    log.refresh_from_db()
    original.refresh_from_db()
    private.refresh_from_db()
    current.refresh_from_db()
    assert log.institutional_clan_id == original.pk
    assert log.credited_private_clan_id == private.pk
    assert log.points_awarded == 10
    assert current.total_points == 0
    assert Notification.objects.filter(user=user).count() == 0
    if has_profile:
        profile = UserProfile.objects.get(user=user)
        assert (profile.total_points, profile.available_points) == (10, 10)
        assert log.status == ActionLog.Status.APPROVED
        assert log.reviewed_by_id == admin.pk
        assert log.reviewed_at is not None
        assert original.total_points == private.total_points == 10
    else:
        assert log.status == ActionLog.Status.PENDING_AUDIT
        assert log.reviewed_by_id is None
        assert log.reviewed_at is None
        assert original.total_points == private.total_points == 0
