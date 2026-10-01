"""Coverage for propagating approved action logs to campaign mission progress."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import NamedTuple

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import (
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.actions.services import notify_mission_progress
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


class MissionWorld(NamedTuple):
    """Objects shared by the mission progress tests."""

    user: User
    clan: Clan
    action: ActionMaster
    campaign: Campaign
    mission: Mission


def _create_campaign(creator: User, **overrides: object) -> Campaign:
    now = timezone.now()
    values: dict[str, object] = {
        "title": "Semana de la bici",
        "scope": Campaign.Scope.GLOBAL,
        "status": Campaign.Status.IN_PROGRESS,
        "creator": creator,
        "start_date": now - timedelta(days=1),
        "end_date": now + timedelta(days=7),
    }
    values.update(overrides)
    return Campaign.objects.create(**values)


def _log_action(world: MissionWorld, **overrides: object) -> ActionLog:
    values: dict[str, object] = {
        "user": world.user,
        "action": world.action,
        "institutional_clan": world.clan,
        "idempotency_key": str(uuid.uuid4()),
        "points_awarded": world.action.points,
        "status": ActionLog.Status.APPROVED,
    }
    values.update(overrides)
    return ActionLog.objects.create(**values)


def _progress(
    world: MissionWorld, mission: Mission | None = None
) -> UserMissionProgress:
    return UserMissionProgress.objects.get(
        user=world.user, mission=mission or world.mission
    )


@pytest.fixture(name="world")
def world_fixture() -> MissionWorld:
    user = create_student()
    action = create_bike_action()
    campaign = _create_campaign(user)
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    mission = Mission.objects.create(campaign=campaign, action=action, target_count=2)
    return MissionWorld(
        user, user.profile.institutional_clan, action, campaign, mission
    )


@pytest.mark.django_db
def test_approved_log_advances_mission_and_records_contribution(
    world: MissionWorld,
) -> None:
    action_log = _log_action(world)

    contributions = notify_mission_progress(action_log)

    progress = _progress(world)
    assert progress.current_count == 1
    assert progress.is_completed is False
    assert [c.mission_id for c in contributions] == [world.mission.id]
    assert ActionLogMissionContribution.objects.filter(
        action_log=action_log, mission=world.mission
    ).exists()


@pytest.mark.django_db
def test_reaching_target_completes_mission_and_stops_counting(
    world: MissionWorld,
) -> None:
    for _ in range(3):
        notify_mission_progress(_log_action(world))

    progress = _progress(world)
    assert progress.current_count == world.mission.target_count
    assert progress.is_completed is True
    # The third log found the mission completed, so it left no contribution.
    assert ActionLogMissionContribution.objects.count() == 2


@pytest.mark.django_db
def test_pending_log_does_not_advance_missions(world: MissionWorld) -> None:
    action_log = _log_action(world, status=ActionLog.Status.PENDING_AUDIT)

    assert notify_mission_progress(action_log) == []
    assert not UserMissionProgress.objects.exists()


@pytest.mark.django_db
def test_user_outside_campaign_does_not_advance_missions(
    world: MissionWorld,
) -> None:
    CampaignParticipant.objects.filter(user=world.user).delete()

    assert notify_mission_progress(_log_action(world)) == []
    assert not UserMissionProgress.objects.exists()


@pytest.mark.django_db
def test_action_logged_before_joining_does_not_count(world: MissionWorld) -> None:
    CampaignParticipant.objects.filter(user=world.user).update(
        joined_at=timezone.now() + timedelta(hours=1)
    )

    assert notify_mission_progress(_log_action(world)) == []


@pytest.mark.django_db
def test_finished_or_out_of_window_campaigns_do_not_advance(
    world: MissionWorld,
) -> None:
    now = timezone.now()
    finished = _create_campaign(world.user, status=Campaign.Status.FINISHED)
    expired = _create_campaign(
        world.user, start_date=now - timedelta(days=9), end_date=now - timedelta(days=2)
    )
    for campaign in (finished, expired):
        CampaignParticipant.objects.create(campaign=campaign, user=world.user)
        Mission.objects.create(campaign=campaign, action=world.action, target_count=1)

    contributions = notify_mission_progress(_log_action(world))

    assert [c.mission_id for c in contributions] == [world.mission.id]


@pytest.mark.django_db
def test_explicit_campaign_limits_progress_to_that_campaign(
    world: MissionWorld,
) -> None:
    other_campaign = _create_campaign(world.user, title="Reto de reciclaje")
    CampaignParticipant.objects.create(campaign=other_campaign, user=world.user)
    other_mission = Mission.objects.create(
        campaign=other_campaign, action=world.action, target_count=5
    )

    notify_mission_progress(_log_action(world, campaign=other_campaign))

    assert _progress(world, other_mission).current_count == 1
    assert not UserMissionProgress.objects.filter(mission=world.mission).exists()


@pytest.mark.django_db
def test_post_action_log_advances_mission(world: MissionWorld) -> None:
    response = post_action_log(world.user, world.action)

    assert response.status_code == 201
    assert _progress(world).current_count == 1


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
    assert _progress(world).current_count == 1
