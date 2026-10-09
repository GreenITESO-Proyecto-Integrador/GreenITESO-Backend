"""Real PostgreSQL/API regressions for atomic approved-points credits."""

from __future__ import annotations

# A full API/DB concurrency scenario intentionally keeps its setup in one test.
# pylint: disable=too-many-locals,too-many-statements
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from django.db import close_old_connections, connection
from rest_framework.test import APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogAuditView, ActionLogCreateView
from green_iteso.notifications.models import Notification


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ("create", "approve"))
def test_concurrent_events_keep_every_profile_and_clan_credit(operation: str) -> None:
    """Different logs for one user must each contribute their frozen points."""
    user = User.objects.create_user(email="credit-user@iteso.mx")
    admin = User.objects.create_user(
        email="credit-admin@iteso.mx", role=User.Role.ADMIN
    )
    institutional = Clan.objects.create(
        name="Credit institutional", type=Clan.ClanType.INSTITUTIONAL, total_points=100
    )
    private = Clan.objects.create(
        name="Credit private", type=Clan.ClanType.PRIVATE, total_points=200
    )
    profile = UserProfile.objects.create(
        user=user,
        institutional_clan=institutional,
        total_points=40,
        available_points=13,
    )
    ClanMembership.objects.create(user=user, clan=private, is_active_private=True)
    category = ActionCategory.objects.create(code="CREDIT", name="Credit")
    action = ActionMaster.objects.create(
        code="CREDIT",
        category=category,
        name="Credit",
        points=10,
        daily_limit=3,
        validation_type=(
            ActionMaster.ValidationType.PHOTO
            if operation == "approve"
            else ActionMaster.ValidationType.NONE
        ),
    )
    keys = [str(uuid.uuid4()), str(uuid.uuid4())]
    pending = []
    if operation == "approve":
        pending = [
            ActionLog.objects.create(
                user=user,
                action=action,
                institutional_clan=institutional,
                credited_private_clan=private,
                idempotency_key=key,
                points_awarded=10,
                status=ActionLog.Status.PENDING_AUDIT,
            )
            for key in keys
        ]
        # Approval uses the ActionLog snapshot, never today's action definition.
        action.points = 99
        action.save(update_fields=["points"])

    window = Barrier(2)
    observed_operations: list[str] = []
    user_table = connection.ops.quote_name(User._meta.db_table)
    profile_table = connection.ops.quote_name(UserProfile._meta.db_table)

    def submit(index: int) -> tuple[int, str]:
        close_old_connections()
        synchronized = False

        def align_balance_operation(
            execute: Any, sql: str, params: Any, many: bool, context: Any
        ) -> Any:
            nonlocal synchronized
            command = sql.lstrip().split(None, 1)[0].upper()
            if synchronized or command != "SELECT":
                return execute(sql, params, many, context)
            if operation == "create" and user_table in sql:
                synchronized = True
                observed_operations.append(command)
                # Rendezvous before the user row lock: create serializes on that
                # lock, so waiting after it would block the second request.
                window.wait(timeout=5)
            elif operation == "approve" and profile_table in sql:
                synchronized = True
                observed_operations.append(command)
                # Complete both unlocked reads before either balance update.
                # F expressions ignore the stale value; the barrier is never
                # placed after acquiring a shared profile/clan row lock.
                result = execute(sql, params, many, context)
                window.wait(timeout=5)
                return result
            return execute(sql, params, many, context)

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
            actor = User.objects.get(pk=admin.pk if operation == "approve" else user.pk)
            factory = APIRequestFactory()
            if operation == "approve":
                request = factory.patch(
                    "/api/v1/action-logs/audit/", {"status": "APPROVED"}, format="json"
                )
                view = ActionLogAuditView.as_view()
                kwargs = {"log_id": str(pending[index].pk)}
            else:
                request = factory.post(
                    "/api/v1/actions/logs/",
                    {
                        "action_id": str(action.pk),
                        "idempotency_key": keys[index],
                    },
                    format="json",
                )
                view = ActionLogCreateView.as_view()
                kwargs = {}
            force_authenticate(request, user=actor)
            with connection.execute_wrapper(align_balance_operation):
                response = view(request, **kwargs)
            return response.status_code, str(response.data.get("log_id", ""))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, index) for index in range(2)]
        results = [future.result(timeout=15) for future in futures]

    assert observed_operations == ["SELECT", "SELECT"]
    assert [status for status, _ in results] == [
        200 if operation == "approve" else 201
    ] * 2
    logs = list(ActionLog.objects.filter(user=user).order_by("idempotency_key"))
    assert len(logs) == 2
    assert {log.idempotency_key for log in logs} == set(keys)
    for log in logs:
        assert log.status == ActionLog.Status.APPROVED
        assert log.points_awarded == 10
        assert log.institutional_clan_id == institutional.pk
        assert log.credited_private_clan_id == private.pk
        if operation == "approve":
            assert log.reviewed_by_id == admin.pk
            assert log.reviewed_at is not None
    notification_types = list(
        Notification.objects.filter(user=user).values_list(
            "notification_type", flat=True
        )
    )
    assert notification_types == (
        [Notification.NotificationType.AUDIT_APPROVED] * 2
        if operation == "approve"
        else []
    )
    profile.refresh_from_db()
    institutional.refresh_from_db()
    private.refresh_from_db()
    assert (profile.total_points, profile.available_points) == (60, 33)
    assert institutional.total_points == 120
    assert private.total_points == 220
