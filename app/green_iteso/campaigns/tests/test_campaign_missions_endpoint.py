"""Tests for the campaign missions endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.actions.models import ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
    Mission,
)


@pytest.mark.django_db
class TestCampaignMissionEndpoint:
    def test_admin_adds_mission_to_global_campaign(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign: Campaign,
        action: ActionMaster,
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": campaign.pk}),
            {"action_id": str(action.pk), "target_count": 3},
            format="json",
        )

        assert response.status_code == 201
        assert campaign.missions.filter(action=action, target_count=3).exists()

    def test_leader_adds_mission_to_private_campaign(
        self,
        api_client: APIClient,
        user: User,
        clan: Clan,
        private_campaign: Campaign,
        action: ActionMaster,
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": private_campaign.pk}),
            {"action_id": str(action.pk), "target_count": 2},
            format="json",
        )

        assert response.status_code == 201

    def test_duplicate_action_returns_400(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign: Campaign,
        mission: Mission,
        action: ActionMaster,
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": campaign.pk}),
            {"action_id": str(action.pk), "target_count": 2},
            format="json",
        )

        assert response.status_code == 400
        assert "action_id" in response.data

    def test_non_manager_gets_403(
        self,
        api_client: APIClient,
        user: User,
        campaign: Campaign,
        action: ActionMaster,
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": campaign.pk}),
            {"action_id": str(action.pk), "target_count": 1},
            format="json",
        )

        assert response.status_code == 403

    def test_campaign_not_in_promotion_returns_400(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign_factory: Any,
        action: ActionMaster,
    ) -> None:
        campaign = campaign_factory(status=Campaign.Status.IN_PROGRESS)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": campaign.pk}),
            {"action_id": str(action.pk), "target_count": 1},
            format="json",
        )

        assert response.status_code == 400
        assert "in promotion" in str(response.data)

    def test_private_campaign_is_404_for_non_member(
        self,
        api_client: APIClient,
        other_user: User,
        private_campaign: Campaign,
        action: ActionMaster,
    ) -> None:
        api_client.force_authenticate(user=other_user)

        response = api_client.post(
            reverse("campaign-missions", kwargs={"campaign_id": private_campaign.pk}),
            {"action_id": str(action.pk), "target_count": 1},
            format="json",
        )

        assert response.status_code == 404

    def test_get_lists_campaign_missions(
        self, api_client: APIClient, user: User, campaign: Campaign, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("campaign-missions", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 200
        assert [item["id"] for item in response.data["results"]] == [str(mission.pk)]
