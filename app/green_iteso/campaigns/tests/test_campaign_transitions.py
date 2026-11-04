"""Tests for POST /campaigns/{id}/start/ and /finish/ and their services."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.services import finish_campaign, start_campaign


def _start_url(campaign: Campaign) -> str:
    return reverse("campaign-start", kwargs={"campaign_id": campaign.pk})


def _finish_url(campaign: Campaign) -> str:
    return reverse("campaign-finish", kwargs={"campaign_id": campaign.pk})


def _in_progress(campaign_factory: Any, **overrides: Any) -> Campaign:
    now = timezone.now()
    return campaign_factory(
        start_date=now - timedelta(hours=2),
        end_date=now + timedelta(days=3),
        status=Campaign.Status.IN_PROGRESS,
        **overrides,
    )


@pytest.mark.django_db
class TestStartEndpoint:
    def test_admin_starts_global_campaign(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        original_end = campaign.end_date
        api_client.force_authenticate(user=admin_user)

        before = timezone.now()
        response = api_client.post(_start_url(campaign))

        assert response.status_code == 200, response.data
        assert response.data["status"] == Campaign.Status.IN_PROGRESS
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.IN_PROGRESS
        assert before <= campaign.start_date <= timezone.now()
        assert campaign.end_date == original_end

    def test_leader_starts_private_campaign(
        self,
        api_client: APIClient,
        user: User,
        clan: Clan,
        private_campaign: Campaign,
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(_start_url(private_campaign))

        assert response.status_code == 200, response.data
        assert response.data["status"] == Campaign.Status.IN_PROGRESS

    @pytest.mark.parametrize(
        "campaign_status", [Campaign.Status.IN_PROGRESS, Campaign.Status.FINISHED]
    )
    def test_non_promotion_campaign_gets_400(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign_factory: Any,
        campaign_status: str,
    ) -> None:
        started = campaign_factory(status=campaign_status)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(_start_url(started))

        assert response.status_code == 400
        started.refresh_from_db()
        assert started.status == campaign_status

    def test_non_manager_gets_403(
        self, api_client: APIClient, user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.post(_start_url(campaign))

        assert response.status_code == 403
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.PROMOTION

    def test_invisible_private_campaign_gets_404(
        self, api_client: APIClient, other_user: User, private_campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=other_user)

        response = api_client.post(_start_url(private_campaign))

        assert response.status_code == 404

    def test_started_campaign_cannot_be_joined(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)
        api_client.post(_start_url(campaign))

        response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400


@pytest.mark.django_db
class TestFinishEndpoint:
    def test_admin_finishes_in_progress_campaign(
        self, api_client: APIClient, admin_user: User, campaign_factory: Any
    ) -> None:
        campaign = _in_progress(campaign_factory)
        api_client.force_authenticate(user=admin_user)

        before = timezone.now()
        response = api_client.post(_finish_url(campaign))

        assert response.status_code == 200, response.data
        assert response.data["status"] == Campaign.Status.FINISHED
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED
        assert before <= campaign.end_date <= timezone.now()

    def test_finish_right_after_start_keeps_end_after_start(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)
        api_client.post(_start_url(campaign))

        response = api_client.post(_finish_url(campaign))

        assert response.status_code == 200, response.data
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.FINISHED
        assert campaign.end_date > campaign.start_date

    @pytest.mark.parametrize(
        "campaign_status", [Campaign.Status.PROMOTION, Campaign.Status.FINISHED]
    )
    def test_non_in_progress_campaign_gets_400(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign_factory: Any,
        campaign_status: str,
    ) -> None:
        target = campaign_factory(status=campaign_status)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(_finish_url(target))

        assert response.status_code == 400
        target.refresh_from_db()
        assert target.status == campaign_status

    def test_non_manager_gets_403(
        self, api_client: APIClient, user: User, campaign_factory: Any
    ) -> None:
        campaign = _in_progress(campaign_factory)
        api_client.force_authenticate(user=user)

        response = api_client.post(_finish_url(campaign))

        assert response.status_code == 403
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.IN_PROGRESS

    def test_invisible_private_campaign_gets_404(
        self,
        api_client: APIClient,
        other_user: User,
        clan: Clan,
        campaign_factory: Any,
    ) -> None:
        campaign = _in_progress(
            campaign_factory, scope=Campaign.Scope.PRIVATE, target_clan=clan
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.post(_finish_url(campaign))

        assert response.status_code == 404


@pytest.mark.django_db
class TestTransitionServices:
    def test_start_campaign_moves_start_to_now(self, campaign: Campaign) -> None:
        started = start_campaign(campaign.pk)

        assert started.status == Campaign.Status.IN_PROGRESS
        assert started.start_date <= timezone.now()

    def test_start_campaign_rejects_pending_proposal(
        self, campaign_factory: Any
    ) -> None:
        proposal = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)

        with pytest.raises(ValidationError):
            start_campaign(proposal.pk)

        proposal.refresh_from_db()
        assert proposal.status == Campaign.Status.PROMOTION

    def test_finish_campaign_moves_end_to_now(self, campaign_factory: Any) -> None:
        campaign = _in_progress(campaign_factory)

        finished = finish_campaign(campaign.pk)

        assert finished.status == Campaign.Status.FINISHED
        assert finished.end_date <= timezone.now()

    def test_finish_campaign_rejects_pending_proposal(
        self, campaign_factory: Any
    ) -> None:
        proposal = _in_progress(
            campaign_factory, approval_status=Campaign.ApprovalStatus.PENDING
        )

        with pytest.raises(ValidationError):
            finish_campaign(proposal.pk)

        proposal.refresh_from_db()
        assert proposal.status == Campaign.Status.IN_PROGRESS
