"""Coverage for GET /api/v1/action-logs/, the administrator audit queue."""

# pylint: disable=duplicate-code

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster

QUEUE_URL = "/api/v1/action-logs/"


def _student(email: str, nickname: str) -> tuple[User, Clan]:
    user = User.objects.create_user(
        email=email, password="local-only", nickname=nickname
    )
    clan = Clan.objects.create(
        name=f"Clan {nickname}", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=clan)
    return user, clan


def _photo_action() -> ActionMaster:
    category = ActionCategory.objects.create(code="AUDIT-QUEUE", name="Audit queue")
    return ActionMaster.objects.create(
        code="PHOTO_QUEUE",
        category=category,
        name="Photo evidence",
        description="Evidence requiring review",
        points=8,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )


def _log(  # pylint: disable=too-many-arguments
    user: User,
    clan: Clan,
    action: ActionMaster,
    *,
    status: str,
    age: timedelta,
    evidence: str,
) -> ActionLog:
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key=f"{user.email}-{status}-{age.total_seconds()}",
        points_awarded=action.points,
        status=status,
        evidence_object_key=evidence,
    )
    ActionLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - age)
    log.refresh_from_db()
    return log


@pytest.mark.django_db
def test_audit_queue_requires_an_administrator() -> None:
    student, _clan = _student("student@iteso.mx", "Ana")
    anonymous = APIClient().get(QUEUE_URL)
    assert anonymous.status_code == 401

    client = APIClient()
    client.force_authenticate(student)
    assert client.get(QUEUE_URL).status_code == 403

    staff = User.objects.create_user(
        email="staff@iteso.mx", password="local-only", role=User.Role.STAFF
    )
    client.force_authenticate(staff)
    assert client.get(QUEUE_URL).status_code == 403


@pytest.mark.django_db
def test_audit_queue_lists_pending_logs_oldest_first() -> None:
    admin = User.objects.create_user(
        email="reviewer@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    older_user, older_clan = _student("older@iteso.mx", "Luis")
    newer_user, newer_clan = _student("newer@iteso.mx", "Mia")
    action = _photo_action()
    older = _log(
        older_user,
        older_clan,
        action,
        status=ActionLog.Status.PENDING_AUDIT,
        age=timedelta(hours=3),
        evidence="demo-only/older.jpg",
    )
    newer = _log(
        newer_user,
        newer_clan,
        action,
        status=ActionLog.Status.PENDING_AUDIT,
        age=timedelta(hours=1),
        evidence="demo-only/newer.jpg",
    )
    _log(
        newer_user,
        newer_clan,
        action,
        status=ActionLog.Status.APPROVED,
        age=timedelta(minutes=5),
        evidence="demo-only/approved.jpg",
    )

    client = APIClient()
    client.force_authenticate(admin)
    response = client.get(QUEUE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [row["id"] for row in body["results"]] == [str(older.id), str(newer.id)]
    first = body["results"][0]
    assert first["status"] == ActionLog.Status.PENDING_AUDIT
    assert first["points_awarded"] == 8
    assert first["evidence_object_key"] == "demo-only/older.jpg"
    assert first["user"] == {
        "id": str(older_user.id),
        "email": "older@iteso.mx",
        "nickname": "Luis",
    }
    assert first["action"] == {
        "id": str(action.id),
        "code": "PHOTO_QUEUE",
        "name": "Photo evidence",
        "validation_type": ActionMaster.ValidationType.PHOTO,
    }


@pytest.mark.django_db
def test_audit_queue_filters_by_status() -> None:
    admin = User.objects.create_user(
        email="reviewer@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    user, clan = _student("filtered@iteso.mx", "Nora")
    action = _photo_action()
    approved = _log(
        user,
        clan,
        action,
        status=ActionLog.Status.APPROVED,
        age=timedelta(hours=2),
        evidence="demo-only/approved.jpg",
    )
    _log(
        user,
        clan,
        action,
        status=ActionLog.Status.PENDING_AUDIT,
        age=timedelta(hours=1),
        evidence="demo-only/pending.jpg",
    )

    client = APIClient()
    client.force_authenticate(admin)
    response = client.get(QUEUE_URL, {"status": ActionLog.Status.APPROVED})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == str(approved.id)
    assert body["results"][0]["status"] == ActionLog.Status.APPROVED


@pytest.mark.django_db
def test_audit_queue_rejects_an_unknown_status() -> None:
    admin = User.objects.create_user(
        email="reviewer@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    client = APIClient()
    client.force_authenticate(admin)

    response = client.get(QUEUE_URL, {"status": "WAITING"})

    assert response.status_code == 400
    assert response.json()["status"] == ["Estado de auditoría no válido."]
