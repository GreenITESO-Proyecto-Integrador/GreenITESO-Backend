"""Nickname changes serialize with onboarding on PostgreSQL."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.db import close_old_connections, connection

from green_iteso.accounts.models import User
from green_iteso.accounts.services import change_nickname, ensure_profile
from green_iteso.clans.services import complete_onboarding


@pytest.mark.django_db(transaction=True)
def test_nickname_change_and_onboarding_do_not_deadlock() -> None:
    """Force overlapping row locks, rather than relying on thread timing."""
    user = User.objects.create_user(
        email="nickname-concurrency@iteso.mx", nickname="Existing"
    )
    ensure_profile(user)
    nickname_locked = threading.Event()
    onboarding_attempted = threading.Event()
    onboarding_locked_user = threading.Event()
    errors: list[Exception] = []

    def change() -> None:
        close_old_connections()

        def pause_after_first_lock(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            result = execute(sql, params, many, context)
            if "FOR UPDATE" in sql and not nickname_locked.is_set():
                nickname_locked.set()
                # With the correct order onboarding waits for our user lock.
                # With the old order let it hold User while we hold UserProfile,
                # reproducing the cycle when each service requests the next row.
                other_lock = (
                    onboarding_attempted
                    if 'FROM "accounts_user"' in sql
                    else onboarding_locked_user
                )
                assert other_lock.wait(10), "Onboarding did not reach its user lock"
            return result

        try:
            with connection.execute_wrapper(pause_after_first_lock):
                change_nickname(user=User.objects.get(pk=user.pk), nickname="Changed")
        except Exception as exc:  # Keep worker failures visible to pytest.
            errors.append(exc)
        finally:
            connection.close()

    def onboard() -> None:
        close_old_connections()

        def observe_user_lock(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            locks_user = "FOR UPDATE" in sql and 'FROM "accounts_user"' in sql
            if locks_user:
                onboarding_attempted.set()
            result = execute(sql, params, many, context)
            if locks_user:
                onboarding_locked_user.set()
            return result

        try:
            assert nickname_locked.wait(10), "Nickname change did not acquire a lock"
            with connection.execute_wrapper(observe_user_lock):
                complete_onboarding(
                    user=User.objects.get(pk=user.pk),
                    career="Review career",
                    nickname="Ignored",
                )
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    workers = [threading.Thread(target=change), threading.Thread(target=onboard)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert not any(worker.is_alive() for worker in workers)
    assert not errors, errors
    user.refresh_from_db()
    profile = ensure_profile(user)
    assert user.nickname == "Changed"
    assert profile.nickname_changes == 1
    assert profile.available_points == 0
    assert profile.career == "Review career"
