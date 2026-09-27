"""Tests for CampaignSerializer."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.actions.models import ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
)
from green_iteso.campaigns.serializers import (
    CampaignSerializer,
)
from green_iteso.campaigns.tests.helpers import FakeRequest, campaign_data


@pytest.mark.django_db
class TestCampaignSerializer:
    def test_global_campaign_without_clan_is_valid(
        self, user: User  # pylint: disable=unused-argument
    ) -> None:
        serializer = CampaignSerializer(data=campaign_data())

        assert serializer.is_valid(), serializer.errors

    def test_global_campaign_with_clan_is_invalid(self, clan: Clan) -> None:
        serializer = CampaignSerializer(data=campaign_data(target_clan=clan.pk))

        assert not serializer.is_valid()
        assert (
            serializer.errors["target_clan"][0]
            == "Global campaigns cannot target a clan."
        )

    def test_private_campaign_without_clan_is_invalid(self) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(scope=Campaign.Scope.PRIVATE)
        )

        assert not serializer.is_valid()
        assert (
            serializer.errors["target_clan"][0]
            == "Private campaigns must target a clan."
        )

    def test_private_campaign_with_clan_is_valid(self, clan: Clan) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=clan.pk)
        )

        assert serializer.is_valid(), serializer.errors

    def test_end_date_must_be_after_start_date(self) -> None:
        current_time = timezone.now()
        serializer = CampaignSerializer(
            data=campaign_data(start_date=current_time, end_date=current_time)
        )

        assert not serializer.is_valid()
        assert serializer.errors["end_date"][0] == "End date must be after start date."

    def test_partial_end_date_update_uses_existing_start_date(
        self, campaign: Campaign
    ) -> None:
        new_end_date = campaign.end_date + timedelta(days=1)
        serializer = CampaignSerializer(
            campaign, data={"end_date": new_end_date}, partial=True
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["end_date"] == new_end_date

    def test_nested_missions_create_with_campaign(
        self, user: User, action: ActionMaster
    ) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(missions=[{"action_id": action.pk, "target_count": 3}])
        )

        assert serializer.is_valid(), serializer.errors
        campaign = serializer.save(creator=user)

        assert Campaign.objects.filter(pk=campaign.pk).exists()
        assert list(campaign.missions.values_list("action_id", "target_count")) == [
            (action.pk, 3)
        ]

    def test_nested_mission_ignores_client_supplied_campaign(
        self, user: User, action: ActionMaster, campaign_factory: Any
    ) -> None:
        other_campaign = campaign_factory()
        serializer = CampaignSerializer(
            data=campaign_data(
                missions=[
                    {
                        "campaign": other_campaign.pk,
                        "action_id": action.pk,
                        "target_count": 3,
                    }
                ]
            )
        )

        assert serializer.is_valid(), serializer.errors
        campaign = serializer.save(creator=user)

        mission = campaign.missions.get()
        assert mission.campaign_id == campaign.pk
        assert mission.campaign_id != other_campaign.pk

    def test_global_scope_requires_admin_role(self, user: User) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(), context={"request": FakeRequest(user=user)}
        )

        with pytest.raises(PermissionDenied):
            serializer.is_valid(raise_exception=True)

    def test_global_scope_allowed_for_admin(self, admin_user: User) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(), context={"request": FakeRequest(user=admin_user)}
        )

        assert serializer.is_valid(), serializer.errors

    def test_private_scope_requires_clan_leader(self, user: User, clan: Clan) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=clan.pk),
            context={"request": FakeRequest(user=user)},
        )

        with pytest.raises(PermissionDenied):
            serializer.is_valid(raise_exception=True)

    def test_private_scope_allowed_for_clan_leader(
        self, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        serializer = CampaignSerializer(
            data=campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=clan.pk),
            context={"request": FakeRequest(user=user)},
        )

        assert serializer.is_valid(), serializer.errors

    def test_podium_snapshot_is_not_serialized(self, campaign: Campaign) -> None:
        campaign.podium_snapshot = {"winner": "hidden"}
        campaign.save(update_fields=["podium_snapshot"])

        assert "podium_snapshot" not in CampaignSerializer(campaign).data
