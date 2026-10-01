"""Coverage for rewinding mission progress when approved evidence is rejected."""

from __future__ import annotations

from datetime import timedelta
from typing import NamedTuple

import pytest
from django.utils import timezone

from green_iteso.accounts.models import User, UserProfile
from green_iteso.actions.models import ActionLogMissionContribution, ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)

from .helpers import (
    audit_action_log,
    create_admin,
    create_bike_action,
    create_student,
    post_action_log,
)


class RevertWorld(NamedTuple):
    """Objects shared by the mission revert tests."""

    user: User
    admin: User
    action: ActionMaster
    mission: Mission


@pytest.fixture(name="world")
def world_fixture() -> RevertWorld:
    user = create_student()
    admin = create_admin()
    action = create_bike_action()
    now = timezone.now()
    campaign = Campaign.objects.create(
        title="Semana de la bici",
        scope=Campaign.Scope.GLOBAL,
        status=Campaign.Status.IN_PROGRESS,
        creator=admin,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=7),
    )
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    mission = Mission.objects.create(campaign=campaign, action=action, target_count=2)
    return RevertWorld(user, admin, action, mission)


def _log_action(world: RevertWorld) -> str:
    response = post_action_log(world.user, world.action)
    assert response.status_code == 201
    return response.json()["log_id"]


def _reject(world: RevertWorld, log_id: str) -> int:
    response = audit_action_log(
        world.admin, log_id, "REJECTED", "La evidencia no corresponde."
    )
    return response.status_code


def _progress(world: RevertWorld) -> tuple[int, bool]:
    progress = UserMissionProgress.objects.get(user=world.user, mission=world.mission)
    return progress.current_count, progress.is_completed


@pytest.mark.django_db
def test_rejecting_log_rewinds_its_mission_increment(world: RevertWorld) -> None:
    log_id = _log_action(world)
    assert _progress(world) == (1, False)

    assert _reject(world, log_id) == 200

    assert _progress(world) == (0, False)
    assert not ActionLogMissionContribution.objects.filter(
        action_log_id=log_id
    ).exists()


@pytest.mark.django_db
def test_rejecting_one_log_keeps_progress_from_others(world: RevertWorld) -> None:
    kept_log_id = _log_action(world)
    rejected_log_id = _log_action(world)
    assert _progress(world) == (2, True)

    assert _reject(world, rejected_log_id) == 200

    # The completed mission falls back to incomplete with one valid action.
    assert _progress(world) == (1, False)
    assert ActionLogMissionContribution.objects.filter(
        action_log_id=kept_log_id
    ).exists()


@pytest.mark.django_db
def test_log_without_contribution_does_not_rewind(world: RevertWorld) -> None:
    _log_action(world)
    _log_action(world)
    # The mission was already completed, so this log contributed nothing.
    extra_log_id = _log_action(world)

    assert _reject(world, extra_log_id) == 200

    assert _progress(world) == (2, True)


@pytest.mark.django_db
def test_blocked_revocation_keeps_mission_progress(world: RevertWorld) -> None:
    log_id = _log_action(world)
    UserProfile.objects.filter(user=world.user).update(available_points=0)

    assert _reject(world, log_id) == 409

    assert _progress(world) == (1, False)
    assert ActionLogMissionContribution.objects.filter(action_log_id=log_id).exists()
