"""Coverage for revoking credited points when an approved action log is rejected."""

from __future__ import annotations

from typing import NamedTuple

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.notifications.models import Notification

from .helpers import (
    audit_action_log,
    create_admin,
    create_bike_action,
    create_student,
    post_action_log,
)

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
    user = create_student()
    private_clan = Clan.objects.create(
        name="Ciclistas", type=Clan.ClanType.PRIVATE, created_by=user
    )
    ClanMembership.objects.create(user=user, clan=private_clan, is_active_private=True)
    return AuditWorld(
        user,
        create_admin(),
        user.profile.institutional_clan,
        private_clan,
        create_bike_action(points=POINTS),
    )


def _log_approved_action(world: AuditWorld) -> str:
    response = post_action_log(world.user, world.action)
    assert response.status_code == 201
    assert response.json()["status"] == ActionLog.Status.APPROVED
    return response.json()["log_id"]


def _audit(world: AuditWorld, log_id: str, decision: str) -> int:
    reason = "La evidencia no corresponde." if decision == "REJECTED" else ""
    return audit_action_log(world.admin, log_id, decision, reason).status_code


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
    response = post_action_log(
        world.user, world.action, evidence_object_key="evidence/bici.jpg"
    )
    assert response.json()["status"] == ActionLog.Status.PENDING_AUDIT

    assert _audit(world, response.json()["log_id"], "REJECTED") == 200

    assert _points(world) == (0, 0, 0, 0)


@pytest.mark.django_db
def test_rejected_log_cannot_be_revoked_twice(world: AuditWorld) -> None:
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

    response = audit_action_log(world.user, log_id, "REJECTED", "Intento propio.")

    assert response.status_code == 403
    assert _points(world) == (POINTS, POINTS, POINTS, POINTS)
