"""Tests for the campaign join endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.urls import reverse
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
)
from green_iteso.campaigns.tests.helpers import campaign_data


@pytest.mark.django_db
class TestCampaignJoinEndpoint:
    @pytest.mark.parametrize(
        "status", [Campaign.Status.PROMOTION, Campaign.Status.IN_PROGRESS]
    )
    def test_join_active_campaign_returns_201(
        self, api_client: APIClient, user: User, campaign_factory: Any, status: str
    ) -> None:
        campaign = campaign_factory(status=status)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 201
        assert CampaignParticipant.objects.filter(campaign=campaign, user=user).exists()

    def test_join_finished_campaign_returns_400(
        self, api_client: APIClient, user: User, campaign_factory: Any
    ) -> None:
        campaign = campaign_factory(status=Campaign.Status.FINISHED)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400
        assert response.data["detail"] == "Campaign is not active."

    def test_join_private_campaign_rejected_without_clan_membership(
        self,
        api_client: APIClient,
        user: User,
        other_user: User,
        clan: Clan,
    ) -> None:
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user,
                scope=Campaign.Scope.PRIVATE,
                target_clan=clan,
                status=Campaign.Status.IN_PROGRESS,
            )
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 403
        assert not CampaignParticipant.objects.filter(
            campaign=campaign, user=other_user
        ).exists()

    def test_join_private_campaign_allowed_for_clan_member(
        self, api_client: APIClient, user: User, other_user: User, clan: Clan
    ) -> None:
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user,
                scope=Campaign.Scope.PRIVATE,
                target_clan=clan,
                status=Campaign.Status.IN_PROGRESS,
            )
        )
        ClanMembership.objects.create(user=other_user, clan=clan)
        api_client.force_authenticate(user=other_user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 201

    def test_join_twice_returns_400(
        self, api_client: APIClient, user: User, campaign: Campaign
    ) -> None:
        CampaignParticipant.objects.create(campaign=campaign, user=user)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400
        assert "already enrolled" in response.data["detail"]

    def test_integrity_error_from_concurrent_join_returns_400(
        self, api_client: APIClient, user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=user)
        with patch.object(
            CampaignParticipant.objects,
            "create",
            side_effect=IntegrityError("campaign_participant_unique"),
        ):
            response = api_client.post(
                reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
            )

        assert response.status_code == 400
        assert "already enrolled" in response.data["detail"]

    def test_join_missing_campaign_is_404(
        self, api_client: APIClient, user: User
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse(
                "campaign-join",
                kwargs={"campaign_id": "00000000-0000-0000-0000-000000000000"},
            )
        )

        assert response.status_code == 404
