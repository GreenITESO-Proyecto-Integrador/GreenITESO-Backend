"""Tests that campaign status is read-only for clients (FR-CAMP-02)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.serializers import CampaignSerializer
from green_iteso.campaigns.tests.helpers import campaign_data


@pytest.mark.django_db
class TestCampaignStatusReadOnly:
    def test_admin_post_ignores_status_and_uses_default(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(status=Campaign.Status.FINISHED),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == Campaign.Status.PROMOTION
        campaign = Campaign.objects.get(pk=response.data["id"])
        assert campaign.status == Campaign.Status.PROMOTION

    def test_partial_update_drops_status_from_validated_data(
        self, campaign: Campaign
    ) -> None:
        serializer = CampaignSerializer(
            campaign, data={"status": Campaign.Status.FINISHED}, partial=True
        )

        assert serializer.is_valid(), serializer.errors
        assert "status" not in serializer.validated_data
