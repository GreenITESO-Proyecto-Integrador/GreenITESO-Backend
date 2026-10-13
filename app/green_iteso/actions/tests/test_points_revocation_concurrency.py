"""Real PostgreSQL regression for concurrent shared-clan credit and revocation."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster


@pytest.mark.django_db(transaction=True)
def test_revocation_and_submission_share_clan_lock_order() -> None:
    """Concurrent submit/reject requests must debit in the same clan order."""
    # pylint: disable=too-many-locals,too-many-statements
    # An index scan can lock PRIVATE before INSTITUTIONAL by UUID,
    # unlike the API's institutional-first credit order.
    private = Clan.objects.create(
        id=UUID(int=1), name="Shared private", type=Clan.ClanType.PRIVATE
    )
    first = User.objects.create_user(email="submitter@iteso.mx", password="local-only")
    institutional = Clan.objects.create(
        name="Shared institution", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=first, institutional_clan=institutional)
    second = User.objects.create_user(email="revokee@iteso.mx", password="local-only")
    UserProfile.objects.create(user=second, institutional_clan=institutional)
    for user in (first, second):
        ClanMembership.objects.create(user=user, clan=private, is_active_private=True)
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    category = ActionCategory.objects.create(code="CONCURRENCY", name="Concurrency")
    action = ActionMaster.objects.create(
        code="CONCURRENT",
        category=category,
        name="Concurrent credit",
        points=50,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    initial_client = APIClient()
    initial_client.force_authenticate(second)
    approved = initial_client.post(
        "/api/v1/action-logs/",
        {"action_id": str(action.pk), "idempotency_key": str(uuid4())},
        format="json",
    )
    assert approved.status_code == 201
    institutional_locked = Event()

    def update_and_pause(
        execute: Callable[..., Any], sql: str, params: Any, many: bool, context: Any
    ) -> Any:
        result = execute(sql, params, many, context)
        if sql.startswith('UPDATE "accounts_clan"') and institutional.pk in params:
            institutional_locked.set()
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE application_name = 'lifecycle-revocation' "
                        "AND datname = current_database()"
                    )
                    if cursor.fetchone() == ("Lock",):
                        break
                sleep(0.01)
            else:
                raise AssertionError("Revocation did not reach competing clan lock")
        return result

    def submit() -> int:
        close_old_connections()
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '8s'")
                with connection.execute_wrapper(update_and_pause):
                    client = APIClient()
                    client.force_authenticate(first)
                    return client.post(
                        "/api/v1/action-logs/",
                        {"action_id": str(action.pk), "idempotency_key": str(uuid4())},
                        format="json",
                    ).status_code
        finally:
            close_old_connections()

    def revoke() -> int:
        close_old_connections()
        try:
            assert institutional_locked.wait(timeout=5)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET LOCAL application_name = 'lifecycle-revocation'"
                    )
                    cursor.execute("SET LOCAL lock_timeout = '8s'")
                    cursor.execute("SET LOCAL enable_seqscan = off")
                    cursor.execute("SET LOCAL enable_bitmapscan = off")
                client = APIClient()
                client.force_authenticate(admin)
                return client.patch(
                    f"/api/v1/action-logs/{approved.json()['log_id']}/audit/",
                    {"status": "REJECTED", "rejection_reason": "Invalid evidence"},
                    format="json",
                ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        submission = pool.submit(submit)
        revocation = pool.submit(revoke)
        assert submission.result(timeout=15) == 201
        assert revocation.result(timeout=15) == 200
    institutional.refresh_from_db()
    private.refresh_from_db()
    assert institutional.total_points == private.total_points == action.points
    assert ActionLog.objects.filter(status=ActionLog.Status.APPROVED).count() == 1
