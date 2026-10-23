"""Tests for PATCH /campaigns/{campaign_id}/."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import Campaign


def _url(campaign: Campaign) -> str:
    return reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})


@pytest.mark.django_db
class TestCampaignUpdateEndpoint:
    def test_admin_edits_global_campaign(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign), {"title": "New title", "description": "New"}, format="json"
        )

        assert response.status_code == 200
        campaign.refresh_from_db()
        assert campaign.title == "New title"
        assert campaign.description == "New"

    def test_leader_edits_private_campaign(
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

        response = api_client.patch(
            _url(private_campaign), {"title": "Clan title"}, format="json"
        )

        assert response.status_code == 200

    def test_admin_edits_institutional_private_campaign(
        self,
        api_client: APIClient,
        admin_user: User,
        user: User,
        campaign_factory: Any,
    ) -> None:
        institutional = Clan.objects.create(
            name="Inst", type=Clan.ClanType.INSTITUTIONAL, created_by=user
        )
        target = campaign_factory(
            scope=Campaign.Scope.PRIVATE, target_clan=institutional
        )
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(_url(target), {"title": "Inst"}, format="json")

        assert response.status_code == 200

    def test_non_manager_gets_403(
        self, api_client: APIClient, user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.patch(_url(campaign), {"title": "x"}, format="json")

        assert response.status_code == 403

    def test_member_non_leader_gets_403(
        self,
        api_client: APIClient,
        other_user: User,
        clan: Clan,
        private_campaign: Campaign,
    ) -> None:
        ClanMembership.objects.create(
            user=other_user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.patch(
            _url(private_campaign), {"title": "x"}, format="json"
        )

        assert response.status_code == 403

    def test_admin_on_private_clan_campaign_gets_403(
        self, api_client: APIClient, admin_user: User, private_campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(private_campaign), {"title": "x"}, format="json"
        )

        assert response.status_code == 403

    def test_invisible_private_campaign_gets_404(
        self, api_client: APIClient, other_user: User, private_campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=other_user)

        response = api_client.patch(
            _url(private_campaign), {"title": "x"}, format="json"
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "approval_status",
        [Campaign.ApprovalStatus.PENDING, Campaign.ApprovalStatus.REJECTED],
    )
    def test_unapproved_campaign_gets_404(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign_factory: Any,
        approval_status: str,
    ) -> None:
        proposal = campaign_factory(
            approval_status=approval_status,
            rejection_reason=(
                "Not suitable"
                if approval_status == Campaign.ApprovalStatus.REJECTED
                else ""
            ),
        )
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(_url(proposal), {"title": "x"}, format="json")

        assert response.status_code == 404

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

        response = api_client.patch(_url(started), {"title": "x"}, format="json")

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "field, value",
        [
            ("scope", "PRIVATE"),
            ("target_clan", None),
            ("missions", []),
            ("status", "IN_PROGRESS"),
            ("approval_status", "PENDING"),
        ],
    )
    def test_non_editable_field_gets_400(
        self,
        api_client: APIClient,
        admin_user: User,
        campaign: Campaign,
        field: str,
        value: Any,
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(_url(campaign), {field: value}, format="json")

        assert response.status_code == 400
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.PROMOTION

    def test_end_before_start_gets_400(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign),
            {"end_date": (campaign.start_date - timedelta(hours=1)).isoformat()},
            format="json",
        )

        assert response.status_code == 400
        assert "end_date" in response.data

    def test_end_in_past_gets_400(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        now = timezone.now()
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign),
            {
                "start_date": (now - timedelta(days=2)).isoformat(),
                "end_date": (now - timedelta(days=1)).isoformat(),
            },
            format="json",
        )

        assert response.status_code == 400
        assert "end_date" in response.data

    def test_past_start_gets_400_and_keeps_promotion(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign),
            {"start_date": (timezone.now() - timedelta(hours=1)).isoformat()},
            format="json",
        )

        assert response.status_code == 400
        assert "start_date" in response.data
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.PROMOTION

    def test_resending_unchanged_dates_does_not_revalidate_them(
        self, api_client: APIClient, admin_user: User, campaign_factory: Any
    ) -> None:
        now = timezone.now()
        stale = campaign_factory(
            start_date=now + timedelta(hours=1), end_date=now + timedelta(hours=5)
        )
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(stale),
            {
                "title": "Same dates",
                "start_date": stale.start_date.isoformat(),
                "end_date": stale.end_date.isoformat(),
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        stale.refresh_from_db()
        assert stale.title == "Same dates"

    def test_title_edit_on_short_legacy_campaign_is_allowed(
        self, api_client: APIClient, admin_user: User, campaign_factory: Any
    ) -> None:
        now = timezone.now()
        legacy = campaign_factory(
            start_date=now + timedelta(hours=1), end_date=now + timedelta(hours=5)
        )
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(_url(legacy), {"title": "Edited"}, format="json")

        assert response.status_code == 200, response.data

    def test_changed_start_is_still_validated(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign),
            {"start_date": (timezone.now() + timedelta(hours=1)).isoformat()},
            format="json",
        )

        assert response.status_code == 400
        assert "start_date" in response.data

    def test_changed_end_less_than_a_day_after_start_gets_400(
        self, api_client: APIClient, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.patch(
            _url(campaign),
            {"end_date": (campaign.start_date + timedelta(hours=12)).isoformat()},
            format="json",
        )

        assert response.status_code == 400
        assert "end_date" in response.data
