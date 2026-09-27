"""Tests for MissionSerializer."""

from __future__ import annotations

import pytest

from green_iteso.actions.models import ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
    Mission,
)
from green_iteso.campaigns.serializers import (
    CampaignSerializer,
    MissionSerializer,
)
from green_iteso.campaigns.tests.helpers import campaign_data


@pytest.mark.django_db
class TestMissionSerializer:
    def test_non_positive_target_count_has_friendly_error(
        self, campaign: Campaign, action: ActionMaster
    ) -> None:
        serializer = MissionSerializer(
            data={"campaign": campaign.pk, "action_id": action.pk, "target_count": 0}
        )

        assert not serializer.is_valid()
        assert (
            serializer.errors["target_count"][0]
            == "Target count must be greater than zero."
        )

    def test_action_exposes_only_public_catalog_fields(self, mission: Mission) -> None:
        data = MissionSerializer(mission).data

        assert data["action"] == {
            "code": "RECYCLE",
            "name": "Recycle",
            "description": "Recycle something",
            "points": 10,
        }

    def test_unknown_action_id_has_validation_error(self, campaign: Campaign) -> None:
        serializer = MissionSerializer(
            data={
                "campaign": campaign.pk,
                "action_id": "00000000-0000-0000-0000-000000000000",
                "target_count": 1,
            }
        )

        assert not serializer.is_valid()
        assert "action_id" in serializer.errors


@pytest.mark.django_db
class TestMissionUniqueness:
    def test_inactive_action_is_rejected(
        self, campaign: Campaign, action: ActionMaster  # pylint: disable=unused-argument
    ) -> None:
        action.is_active = False
        action.save(update_fields=["is_active"])
        serializer = MissionSerializer(data={"action_id": action.pk, "target_count": 1})

        assert not serializer.is_valid()
        assert (
            serializer.errors["action_id"][0] == "Action does not exist or is inactive."
        )

    def test_nested_duplicate_actions_are_rejected(self, action: ActionMaster) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(
                missions=[
                    {"action_id": action.pk, "target_count": 1},
                    {"action_id": action.pk, "target_count": 2},
                ]
            )
        )

        assert not serializer.is_valid()
        assert (
            serializer.errors["missions"][0]
            == "Each action can appear only once per campaign."
        )

    def test_action_with_existing_mission_is_rejected(
        self,
        campaign: Campaign,
        mission: Mission,  # pylint: disable=unused-argument
        action: ActionMaster,
    ) -> None:
        serializer = MissionSerializer(
            data={"action_id": action.pk, "target_count": 1},
            context={"campaign": campaign},
        )

        assert not serializer.is_valid()
        assert (
            serializer.errors["action_id"][0]
            == "This action already has a mission in this campaign."
        )
