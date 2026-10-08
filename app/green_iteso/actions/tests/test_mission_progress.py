"""Coverage for when action logs are propagated to campaign mission progress.

Mission eligibility rules belong to the campaigns domain and are covered by
``campaigns/tests/test_mission_progress_derivation.py``; these tests check that
the actions endpoints call it at the right moment.
"""

from __future__ import annotations

import uuid
from typing import NamedTuple

import pytest

from green_iteso.accounts.models import User
from green_iteso.actions.models import (
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.actions.services import notify_mission_progress
from green_iteso.campaigns.models import Mission, UserMissionProgress

from .helpers import (
    audit_action_log,
    create_admin,
    create_bike_action,
    create_mission_for,
    create_student,
    post_action_log,
)


class MissionWorld(NamedTuple):
    """Objects shared by the mission progress tests."""

    user: User
    action: ActionMaster
    mission: Mission


@pytest.fixture(name="world")
def world_fixture() -> MissionWorld:
    user = create_student()
    action = create_bike_action()
    return MissionWorld(user, action, create_mission_for(user, action))


def _current_count(world: MissionWorld) -> int:
    return UserMissionProgress.objects.get(
        user=world.user, mission=world.mission
    ).current_count


@pytest.mark.django_db
def test_pending_log_does_not_advance_missions(world: MissionWorld) -> None:
    action_log = ActionLog.objects.create(
        user=world.user,
        action=world.action,
        institutional_clan=world.user.profile.institutional_clan,
        idempotency_key=str(uuid.uuid4()),
        points_awarded=world.action.points,
        status=ActionLog.Status.PENDING_AUDIT,
    )

    assert not notify_mission_progress(action_log)
    assert not UserMissionProgress.objects.exists()


@pytest.mark.django_db
def test_post_action_log_advances_mission(world: MissionWorld) -> None:
    response = post_action_log(world.user, world.action)

    assert response.status_code == 201
    assert _current_count(world) == 1
    assert ActionLogMissionContribution.objects.filter(
        action_log_id=response.json()["log_id"], mission=world.mission
    ).exists()


@pytest.mark.django_db
def test_photo_action_advances_mission_only_after_audit_approval(
    world: MissionWorld,
) -> None:
    world.action.validation_type = ActionMaster.ValidationType.PHOTO
    world.action.save(update_fields=["validation_type"])

    create_response = post_action_log(
        world.user, world.action, evidence_object_key="evidence/bici.jpg"
    )
    assert create_response.status_code == 201
    assert not UserMissionProgress.objects.exists()

    audit_response = audit_action_log(
        create_admin(), create_response.json()["log_id"], "APPROVED"
    )

    assert audit_response.status_code == 200
    assert _current_count(world) == 1
