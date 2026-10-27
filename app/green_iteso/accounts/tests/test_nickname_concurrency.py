"""Nickname changes serialize with onboarding on PostgreSQL."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.db import close_old_connections, connection
from rest_framework.test import APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.accounts.services import change_nickname, ensure_profile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogAuditView
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


@pytest.mark.django_db(transaction=True)
def test_nickname_change_does_not_deadlock_with_action_approval() -> None:
    """Approval's notification FK check must coexist with a nickname lock."""
    user = User.objects.create_user(
        email="nickname-audit-concurrency@iteso.mx", nickname="Existing"
    )
    admin = User.objects.create_user(
        email="nickname-audit-admin@iteso.mx", role=User.Role.ADMIN
    )
    clan = Clan.objects.create(name="Audit clan", type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    category = ActionCategory.objects.create(code="AUDIT", name="Audit")
    action = ActionMaster.objects.create(
        code="AUDIT_PHOTO",
        category=category,
        name="Audit photo",
        points=10,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="nickname-audit-concurrency",
        points_awarded=10,
        status=ActionLog.Status.PENDING_AUDIT,
    )
    nickname_user_locked = threading.Event()
    approval_profile_updated = threading.Event()
    errors: list[Exception] = []
    statuses: list[int] = []

    def change() -> None:
        close_old_connections()

        def coordinate_after_user_lock(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            result = execute(sql, params, many, context)
            if 'FROM "accounts_user"' in sql and "FOR " in sql:
                nickname_user_locked.set()
                assert approval_profile_updated.wait(10), (
                    "Approval did not update the profile before notification"
                )
            return result

        try:
            with connection.execute_wrapper(coordinate_after_user_lock):
                change_nickname(user=User.objects.get(pk=user.pk), nickname="Changed")
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    def approve() -> None:
        close_old_connections()

        def observe_profile_update(
            execute: Callable[..., Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE") and '"accounts_userprofile"' in sql:
                approval_profile_updated.set()
            return result

        try:
            assert nickname_user_locked.wait(10), "Nickname change did not lock User"
            request = APIRequestFactory().patch(
                "/api/v1/action-logs/audit/", {"status": "APPROVED"}, format="json"
            )
            force_authenticate(request, user=admin)
            with connection.execute_wrapper(observe_profile_update):
                response = ActionLogAuditView.as_view()(request, log_id=str(log.pk))
            statuses.append(response.status_code)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    workers = [threading.Thread(target=change), threading.Thread(target=approve)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert not any(worker.is_alive() for worker in workers)
    assert not errors, errors
    assert statuses == [200]
    log.refresh_from_db()
    user.refresh_from_db()
    profile = ensure_profile(user)
    assert log.status == ActionLog.Status.APPROVED
    assert user.nickname == "Changed"
    assert profile.available_points == 10
