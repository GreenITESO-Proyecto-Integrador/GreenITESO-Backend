"""Coverage for revoking credited points when an approved action log is rejected."""

from __future__ import annotations

import uuid
from typing import NamedTuple

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.notifications.models import Notification

POINTS = 50


class AuditWorld(NamedTuple):
    """Objects shared by the revocation tests."""

    user: User
    admin: User
    institutional_clan: Clan
    private_clan: Clan
    action: ActionMaster


@pytest.fixture(name="world")
def world_fixture() -> AuditWorld:
    user = User.objects.create_user(email="student@iteso.mx", password="local-only")
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    private_clan = Clan.objects.create(
        name="Ciclistas", type=Clan.ClanType.PRIVATE, created_by=user
    )
    UserProfile.objects.create(user=user, institutional_clan=institutional_clan)
    ClanMembership.objects.create(user=user, clan=private_clan, is_active_private=True)
    category = ActionCategory.objects.create(code="MOBILITY", name="Movilidad")
    action = ActionMaster.objects.create(
        code="BIKE",
        category=category,
        name="Uso de Bicicleta",
        description="Llegar en bici al campus",
        points=POINTS,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    return AuditWorld(user, admin, institutional_clan, private_clan, action)


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _log_approved_action(world: AuditWorld) -> str:
    response = _client(world.user).post(
        "/api/v1/action-logs/",
        {"action_id": str(world.action.id), "idempotency_key": str(uuid.uuid4())},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["status"] == ActionLog.Status.APPROVED
    return response.json()["log_id"]


def _audit(world: AuditWorld, log_id: str, decision: str) -> int:
    payload = {"status": decision}
    if decision == "REJECTED":
        payload["rejection_reason"] = "La evidencia no corresponde."
    response = _client(world.admin).patch(
        f"/api/v1/action-logs/{log_id}/audit/", payload, format="json"
    )
    return response.status_code


def _points(world: AuditWorld) -> tuple[int, int, int, int]:
    profile = UserProfile.objects.get(user=world.user)
    institutional = Clan.all_objects.get(pk=world.institutional_clan.pk)
    private = Clan.all_objects.get(pk=world.private_clan.pk)
    return (
        profile.total_points,
        profile.available_points,
        institutional.total_points,
        private.total_points,
    )


@pytest.mark.django_db
def test_rejecting_approved_log_deducts_awarded_points(world: AuditWorld) -> None:
    log_id = _log_approved_action(world)
    assert _points(world) == (POINTS, POINTS, POINTS, POINTS)

    assert _audit(world, log_id, "REJECTED") == 200

    assert _points(world) == (0, 0, 0, 0)
    assert ActionLog.objects.get(pk=log_id).status == ActionLog.Status.REJECTED
    assert Notification.objects.filter(
        user=world.user,
        notification_type=Notification.NotificationType.AUDIT_REJECT,
    ).exists()


@pytest.mark.django_db
def test_deduction_uses_frozen_points_and_clans(world: AuditWorld) -> None:
    log_id = _log_approved_action(world)
    # Later changes to the catalog and to the user's active private clan must
    # not alter what is revoked.
    world.action.points = 999
    world.action.save(update_fields=["points"])
    ClanMembership.objects.filter(user=world.user).update(is_active_private=False)
    new_clan = Clan.objects.create(
        name="Recicladores", type=Clan.ClanType.PRIVATE, total_points=POINTS
    )
    ClanMembership.objects.create(
        user=world.user, clan=new_clan, is_active_private=True
    )

    assert _audit(world, log_id, "REJECTED") == 200

    assert _points(world) == (0, 0, 0, 0)
    new_clan.refresh_from_db()
    assert new_clan.total_points == POINTS


@pytest.mark.django_db
def test_dissolved_clan_is_still_debited(world: AuditWorld) -> None:
    log_id = _log_approved_action(world)
    Clan.all_objects.filter(pk=world.private_clan.pk).update(deleted_at=timezone.now())

    assert _audit(world, log_id, "REJECTED") == 200

    assert _points(world)[3] == 0


@pytest.mark.django_db
def test_rejecting_pending_log_does_not_touch_points(world: AuditWorld) -> None:
    world.action.validation_type = ActionMaster.ValidationType.PHOTO
    world.action.save(update_fields=["validation_type"])
    response = _client(world.user).post(
        "/api/v1/action-logs/",
        {
            "action_id": str(world.action.id),
            "idempotency_key": str(uuid.uuid4()),
            "evidence_object_key": "evidence/bici.jpg",
        },
        format="json",
    )
    assert response.json()["status"] == ActionLog.Status.PENDING_AUDIT

    assert _audit(world, response.json()["log_id"], "REJECTED") == 200

    assert _points(world) == (0, 0, 0, 0)


@pytest.mark.django_db
def test_rejected_log_cannot_be_revoked_twice(world: AuditWorld) -> None:
    world.action.daily_limit = 2
    world.action.save(update_fields=["daily_limit"])
    log_id = _log_approved_action(world)
    _log_approved_action(world)
    assert _audit(world, log_id, "REJECTED") == 200

    assert _audit(world, log_id, "REJECTED") == 404

    assert _points(world) == (POINTS, POINTS, POINTS, POINTS)


@pytest.mark.django_db
def test_approved_log_cannot_be_approved_again(world: AuditWorld) -> None:
    log_id = _log_approved_action(world)

    assert _audit(world, log_id, "APPROVED") == 404

    assert _points(world) == (POINTS, POINTS, POINTS, POINTS)


@pytest.mark.django_db
def test_spent_points_block_revocation_without_side_effects(
    world: AuditWorld,
) -> None:
    log_id = _log_approved_action(world)
    UserProfile.objects.filter(user=world.user).update(available_points=POINTS - 1)

    assert _audit(world, log_id, "REJECTED") == 409

    assert _points(world) == (POINTS, POINTS - 1, POINTS, POINTS)
    assert ActionLog.objects.get(pk=log_id).status == ActionLog.Status.APPROVED
    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_only_admins_can_revoke(world: AuditWorld) -> None:
    log_id = _log_approved_action(world)

    response = _client(world.user).patch(
        f"/api/v1/action-logs/{log_id}/audit/",
        {"status": "REJECTED", "rejection_reason": "Intento propio."},
        format="json",
    )

    assert response.status_code == 403
    assert _points(world) == (POINTS, POINTS, POINTS, POINTS)
