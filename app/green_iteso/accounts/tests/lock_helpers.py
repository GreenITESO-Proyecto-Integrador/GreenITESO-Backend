"""Shared harness for tests that race a change to a user against an approval.

An admin approving an action updates the user's profile and then inserts a
notification, whose foreign key needs a KEY SHARE lock on the user. A service
that locks the user and then waits for that profile row must therefore take its
user lock with NO KEY UPDATE, or the two transactions deadlock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from django.db import close_old_connections, connection
from rest_framework.test import APIRequestFactory, force_authenticate

from green_iteso.accounts.models import User
from green_iteso.actions.models import ActionLog
from green_iteso.actions.views import ActionLogAuditView


def run_workers(workers: list[threading.Thread]) -> None:
    """Start concurrent workers and ensure each finishes within the test."""
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
    assert not any(worker.is_alive() for worker in workers)


class ApprovalRace:
    """Run ``change`` while an admin approves ``log``, forcing the lock overlap.

    ``change`` runs in one thread and is paused right after it takes its user
    lock until the approval thread has updated the profile. Worker failures are
    collected so pytest reports them instead of a bare timeout.
    """

    def __init__(self, *, admin: User, log: ActionLog) -> None:
        self.admin = admin
        self.log = log
        self.user_locked = threading.Event()
        self.profile_updated = threading.Event()
        self.errors: list[Exception] = []
        self.statuses: list[int] = []

    def run(self, change: Callable[[], Any]) -> None:
        """Race ``change`` against the approval and require a clean outcome."""
        workers = [
            threading.Thread(target=self._change, args=(change,)),
            threading.Thread(target=self._approve),
        ]
        run_workers(workers)
        assert not self.errors, self.errors
        assert self.statuses == [200]

    def _change(self, change: Callable[[], Any]) -> None:
        close_old_connections()

        def pause_after_user_lock(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            result = execute(sql, params, many, context)
            normalized_sql = sql.upper()
            if (
                'FROM "ACCOUNTS_USER"' in normalized_sql
                and " FOR " in normalized_sql
                and not self.user_locked.is_set()
            ):
                self.user_locked.set()
                assert self.profile_updated.wait(10), (
                    "Approval did not update the profile before notification"
                )
            return result

        try:
            with connection.execute_wrapper(pause_after_user_lock):
                change()
        except Exception as exc:  # Keep worker failures visible to pytest.
            self.errors.append(exc)
        finally:
            connection.close()

    def _approve(self) -> None:
        close_old_connections()

        def observe_profile_update(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            result = execute(sql, params, many, context)
            normalized_sql = sql.lstrip().upper()
            if (
                normalized_sql.startswith("UPDATE")
                and "ACCOUNTS_USER_PROFILE" in normalized_sql
            ):
                self.profile_updated.set()
            return result

        try:
            assert self.user_locked.wait(10), "The change did not lock User"
            request = APIRequestFactory().patch(
                "/api/v1/action-logs/audit/", {"status": "APPROVED"}, format="json"
            )
            force_authenticate(request, user=self.admin)
            with connection.execute_wrapper(observe_profile_update):
                response = ActionLogAuditView.as_view()(
                    request, log_id=str(self.log.pk)
                )
            self.statuses.append(response.status_code)
        except Exception as exc:
            self.errors.append(exc)
        finally:
            connection.close()
