"""Tests for deriving mission progress from ActionLog contributions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import (
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)
from green_iteso.campaigns.services import (
    apply_action_log_to_missions,
    recalculate_mission_progress,
    revert_action_log_from_missions,
    validate_action_log_campaign,
)


def _make_action_log(
    *,
    user: User,
    action: ActionMaster,
    clan: Clan,
    key: str,
    status: str = ActionLog.Status.APPROVED,
) -> ActionLog:
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key=key,
        points_awarded=action.points,
        status=status,
    )


@pytest.fixture
def institutional_clan(admin_user: User) -> Clan:
    return Clan.objects.create(
        name="Institutional clan",
        type=Clan.ClanType.INSTITUTIONAL,
        created_by=admin_user,
    )


@pytest.fixture
def in_progress_campaign(campaign_factory: Any) -> Campaign:
    now = timezone.now()
    return campaign_factory(
        start_date=now - timedelta(hours=1),
        end_date=now + timedelta(days=1),
        status=Campaign.Status.IN_PROGRESS,
    )


@pytest.fixture
def in_progress_mission(
    in_progress_campaign: Campaign, action: ActionMaster, user: User
) -> Mission:
    mission = Mission.objects.create(
        campaign=in_progress_campaign, action=action, target_count=3
    )
    CampaignParticipant.objects.create(campaign=in_progress_campaign, user=user)
    return mission


@pytest.mark.django_db
class TestValidateActionLogCampaign:
    def test_none_campaign_id_returns_none(
        self, user: User, action: ActionMaster
    ) -> None:
        assert validate_action_log_campaign(user, action, None) is None

    def test_success_returns_campaign(
        self,
        user: User,
        action: ActionMaster,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        result = validate_action_log_campaign(user, action, in_progress_campaign.pk)
        assert result == in_progress_campaign

    def test_rejects_promotion_campaign(
        self, user: User, action: ActionMaster, campaign_factory: Any
    ) -> None:
        campaign = campaign_factory(status=Campaign.Status.PROMOTION)
        Mission.objects.create(campaign=campaign, action=action, target_count=1)
        CampaignParticipant.objects.create(campaign=campaign, user=user)

        with pytest.raises(ValidationError):
            validate_action_log_campaign(user, action, campaign.pk)

    def test_rejects_finished_campaign(
        self, user: User, action: ActionMaster, campaign_factory: Any
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            start_date=now - timedelta(days=2),
            end_date=now - timedelta(days=1),
            status=Campaign.Status.FINISHED,
        )
        Mission.objects.create(campaign=campaign, action=action, target_count=1)
        CampaignParticipant.objects.create(campaign=campaign, user=user)

        with pytest.raises(ValidationError):
            validate_action_log_campaign(user, action, campaign.pk)

    def test_rejects_pending_campaign(
        self, user: User, action: ActionMaster, campaign_factory: Any
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=1),
            status=Campaign.Status.IN_PROGRESS,
            approval_status=Campaign.ApprovalStatus.PENDING,
        )

        with pytest.raises(ValidationError):
            validate_action_log_campaign(user, action, campaign.pk)

    def test_rejects_non_participant(
        self,
        other_user: User,
        action: ActionMaster,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        with pytest.raises(ValidationError):
            validate_action_log_campaign(other_user, action, in_progress_campaign.pk)

    def test_rejects_campaign_without_mission_for_action(
        self,
        user: User,
        second_action: ActionMaster,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        CampaignParticipant.objects.filter(
            campaign=in_progress_campaign, user=user
        ).exists()
        with pytest.raises(ValidationError):
            validate_action_log_campaign(user, second_action, in_progress_campaign.pk)


@pytest.mark.django_db
class TestApplyActionLogToMissions:
    def test_increments_progress_in_all_matching_campaigns(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        log = _make_action_log(
            user=user, action=action, clan=institutional_clan, key="k1"
        )

        progresses = apply_action_log_to_missions(log)

        assert len(progresses) == 1
        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 1
        assert progress.is_completed is False

    def test_does_not_affect_promotion_or_finished_campaigns(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        campaign_factory: Any,
    ) -> None:
        now = timezone.now()
        promo_campaign = campaign_factory(status=Campaign.Status.PROMOTION)
        promo_mission = Mission.objects.create(
            campaign=promo_campaign, action=action, target_count=2
        )
        CampaignParticipant.objects.create(campaign=promo_campaign, user=user)

        finished_campaign = campaign_factory(
            start_date=now - timedelta(days=2),
            end_date=now - timedelta(days=1),
            status=Campaign.Status.FINISHED,
        )
        finished_mission = Mission.objects.create(
            campaign=finished_campaign, action=action, target_count=2
        )
        CampaignParticipant.objects.create(campaign=finished_campaign, user=user)

        log = _make_action_log(
            user=user, action=action, clan=institutional_clan, key="k2"
        )
        apply_action_log_to_missions(log)

        assert not UserMissionProgress.objects.filter(mission=promo_mission).exists()
        assert not UserMissionProgress.objects.filter(mission=finished_mission).exists()

    def test_does_not_affect_campaign_where_not_participant(
        self,
        other_user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        log = _make_action_log(
            user=other_user, action=action, clan=institutional_clan, key="k3"
        )

        progresses = apply_action_log_to_missions(log)

        assert progresses == []
        assert not UserMissionProgress.objects.filter(user=other_user).exists()

    def test_is_idempotent(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        log = _make_action_log(
            user=user, action=action, clan=institutional_clan, key="k4"
        )

        apply_action_log_to_missions(log)
        apply_action_log_to_missions(log)

        assert (
            ActionLogMissionContribution.objects.filter(
                action_log=log, mission=in_progress_mission
            ).count()
            == 1
        )
        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 1

    def test_marks_completed_at_target(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        for index in range(3):
            log = _make_action_log(
                user=user, action=action, clan=institutional_clan, key=f"k5-{index}"
            )
            apply_action_log_to_missions(log)

        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 3
        assert progress.is_completed is True

    def test_does_not_exceed_target_but_creates_contribution(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        logs = [
            _make_action_log(
                user=user, action=action, clan=institutional_clan, key=f"k6-{index}"
            )
            for index in range(4)
        ]
        for log in logs:
            apply_action_log_to_missions(log)

        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 3
        assert progress.is_completed is True
        assert (
            ActionLogMissionContribution.objects.filter(
                mission=in_progress_mission
            ).count()
            == 4
        )

    def test_rejected_log_does_nothing(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        log = _make_action_log(
            user=user,
            action=action,
            clan=institutional_clan,
            key="k7",
            status=ActionLog.Status.REJECTED,
        )

        progresses = apply_action_log_to_missions(log)

        assert progresses == []
        assert not ActionLogMissionContribution.objects.filter(action_log=log).exists()


@pytest.mark.django_db
class TestRevertActionLogFromMissions:
    def test_reduces_progress_and_unmarks_completed(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        logs = [
            _make_action_log(
                user=user, action=action, clan=institutional_clan, key=f"k8-{index}"
            )
            for index in range(3)
        ]
        for log in logs:
            apply_action_log_to_missions(log)
        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.is_completed is True

        rejected = logs[0]
        rejected.status = ActionLog.Status.REJECTED
        rejected.save(update_fields=["status"])
        revert_action_log_from_missions(rejected)

        progress.refresh_from_db()
        assert progress.current_count == 2
        assert progress.is_completed is False

    def test_stays_completed_when_extra_logs_remain(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        logs = [
            _make_action_log(
                user=user, action=action, clan=institutional_clan, key=f"k9-{index}"
            )
            for index in range(4)
        ]
        for log in logs:
            apply_action_log_to_missions(log)

        rejected = logs[0]
        rejected.status = ActionLog.Status.REJECTED
        rejected.save(update_fields=["status"])
        revert_action_log_from_missions(rejected)

        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 3
        assert progress.is_completed is True

    def test_works_when_campaign_finished(
        self,
        user: User,
        action: ActionMaster,
        institutional_clan: Clan,
        in_progress_campaign: Campaign,
        in_progress_mission: Mission,
    ) -> None:
        log = _make_action_log(
            user=user, action=action, clan=institutional_clan, key="k10"
        )
        apply_action_log_to_missions(log)

        in_progress_campaign.status = Campaign.Status.FINISHED
        in_progress_campaign.save(update_fields=["status"])

        log.status = ActionLog.Status.REJECTED
        log.save(update_fields=["status"])
        progresses = revert_action_log_from_missions(log)

        assert len(progresses) == 1
        progress = UserMissionProgress.objects.get(
            user=user, mission=in_progress_mission
        )
        assert progress.current_count == 0
        assert progress.is_completed is False


@pytest.mark.django_db
class TestRecalculateMissionProgress:
    def test_creates_progress_row(
        self, user: User, in_progress_mission: Mission
    ) -> None:
        progress = recalculate_mission_progress(user, in_progress_mission)
        assert progress.current_count == 0
        assert progress.is_completed is False
