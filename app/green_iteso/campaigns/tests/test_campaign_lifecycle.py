"""Tests for the automatic campaign lifecycle (FR-CAMP-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.services import (
    compute_campaign_status,
    sync_campaign_statuses,
)
from green_iteso.campaigns.tests.helpers import campaign_data

DAY = timedelta(days=1)


class TestComputeCampaignStatus:
    def test_before_start_is_promotion(self) -> None:
        now = timezone.now()

        assert (
            compute_campaign_status(now + DAY, now + 2 * DAY, now)
            == Campaign.Status.PROMOTION
        )

    def test_between_dates_is_in_progress(self) -> None:
        now = timezone.now()

        assert (
            compute_campaign_status(now - DAY, now + DAY, now)
            == Campaign.Status.IN_PROGRESS
        )

    def test_after_end_is_finished(self) -> None:
        now = timezone.now()

        assert (
            compute_campaign_status(now - 2 * DAY, now - DAY, now)
            == Campaign.Status.FINISHED
        )

    def test_now_equal_to_start_is_in_progress(self) -> None:
        now = timezone.now()

        assert (
            compute_campaign_status(now, now + DAY, now) == Campaign.Status.IN_PROGRESS
        )

    def test_now_equal_to_end_is_finished(self) -> None:
        now = timezone.now()

        assert compute_campaign_status(now - DAY, now, now) == Campaign.Status.FINISHED


@pytest.mark.django_db
class TestSyncCampaignStatuses:
    def test_promotion_to_in_progress(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - DAY,
            end_date=now + DAY,
        )

        assert sync_campaign_statuses(now) == 1

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.IN_PROGRESS

    def test_promotion_to_finished(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )

        assert sync_campaign_statuses(now) == 1

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED

    def test_in_progress_to_finished(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.IN_PROGRESS,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )

        assert sync_campaign_statuses(now) == 1

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED

    def test_correct_campaigns_are_untouched(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now + DAY,
            end_date=now + 2 * DAY,
        )
        campaign_factory(
            status=Campaign.Status.IN_PROGRESS,
            start_date=now - DAY,
            end_date=now + DAY,
        )

        assert sync_campaign_statuses(now) == 0

    def test_finished_is_never_reverted(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.FINISHED,
            start_date=now - DAY,
            end_date=now + DAY,
        )

        assert sync_campaign_statuses(now) == 0

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED

    def test_returns_total_updated(self, campaign_factory: Callable) -> None:
        now = timezone.now()
        campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - DAY,
            end_date=now + DAY,
        )
        campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )
        campaign_factory(
            status=Campaign.Status.IN_PROGRESS,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )

        assert sync_campaign_statuses(now) == 3
        assert sync_campaign_statuses(now) == 0


@pytest.mark.django_db
class TestLifecycleEndpoints:
    def test_post_future_start_is_promotion(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        now = timezone.now()
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(start_date=now + DAY, end_date=now + 2 * DAY),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == Campaign.Status.PROMOTION

    def test_post_past_start_is_in_progress(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        now = timezone.now()
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(start_date=now - DAY, end_date=now + DAY),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == Campaign.Status.IN_PROGRESS

    def test_post_past_end_returns_400(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        now = timezone.now()
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(start_date=now - 2 * DAY, end_date=now - DAY),
            format="json",
        )

        assert response.status_code == 400
        assert response.data["end_date"][0] == "End date must be in the future."

    def test_list_syncs_expired_campaign(
        self, api_client: APIClient, user: User, campaign_factory: Callable
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(reverse("campaign-list"))

        assert response.status_code == 200
        assert response.data["results"][0]["status"] == Campaign.Status.FINISHED
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED

    def test_join_expired_promotion_campaign_returns_400(
        self, api_client: APIClient, user: User, campaign_factory: Callable
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400
        assert response.data["detail"] == "Campaign is not active."


@pytest.mark.django_db
class TestSyncCampaignStatusesCommand:
    def test_command_updates_status_and_prints_count(
        self, campaign_factory: Callable
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )
        out = StringIO()

        call_command("sync_campaign_statuses", stdout=out)

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED
        assert "Updated 1 campaign(s)." in out.getvalue()
