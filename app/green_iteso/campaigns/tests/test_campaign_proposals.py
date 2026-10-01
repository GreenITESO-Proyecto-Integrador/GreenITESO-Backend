"""Tests for global campaign proposals and admin review (FR-CAMP-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import ActionMaster
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.services import (
    approve_campaign,
    reject_campaign,
    sync_campaign_statuses,
)
from green_iteso.campaigns.tests.helpers import campaign_data, default_missions

DAY = timedelta(days=1)


def proposal_payload(**overrides: object) -> dict[str, object]:
    now = timezone.now()
    data: dict[str, object] = {
        "title": "Proposed campaign",
        "description": "A proposal",
        "start_date": now + timedelta(hours=1),
        "end_date": now + timedelta(days=7),
        "missions": default_missions(),
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestProposeGlobalCampaign:
    @pytest.mark.parametrize("role", [User.Role.STUDENT, User.Role.STAFF])
    def test_non_admin_can_propose(
        self, api_client: APIClient, user: User, role: str
    ) -> None:
        user.role = role
        user.save(update_fields=["role"])
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )

        assert response.status_code == 201, response.data
        assert response.data["approval_status"] == Campaign.ApprovalStatus.PENDING
        assert response.data["scope"] == Campaign.Scope.GLOBAL
        assert response.data["creator"] == user.pk
        campaign = Campaign.objects.get(pk=response.data["id"])
        assert campaign.approval_status == Campaign.ApprovalStatus.PENDING
        assert campaign.scope == Campaign.Scope.GLOBAL
        assert campaign.creator_id == user.pk

    def test_proposal_with_missions_creates_them(
        self, api_client: APIClient, user: User, action: ActionMaster
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-proposals"),
            proposal_payload(missions=[{"action_id": action.pk, "target_count": 3}]),
            format="json",
        )

        assert response.status_code == 201, response.data
        campaign = Campaign.objects.get(pk=response.data["id"])
        assert campaign.missions.count() == 1

    def test_admin_cannot_propose(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )

        assert response.status_code == 403
        assert Campaign.objects.count() == 0


@pytest.mark.django_db
class TestPendingProposalVisibility:
    def test_pending_proposal_hidden_from_campaign_list(
        self, api_client: APIClient, user: User, other_user: User
    ) -> None:
        api_client.force_authenticate(user=user)
        response = api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )
        campaign_id = response.data["id"]
        api_client.force_authenticate(user=other_user)

        list_response = api_client.get(reverse("campaign-list"))
        detail_response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign_id})
        )
        join_response = api_client.post(
            reverse("campaign-join", kwargs={"campaign_id": campaign_id})
        )

        assert all(
            campaign["id"] != campaign_id for campaign in list_response.data["results"]
        )
        assert detail_response.status_code == 404
        assert join_response.status_code == 404


@pytest.mark.django_db
class TestListProposals:
    def test_admin_sees_all_proposals(
        self, api_client: APIClient, user: User, other_user: User, admin_user: User
    ) -> None:
        for proposer in (user, other_user):
            api_client.force_authenticate(user=proposer)
            api_client.post(
                reverse("campaign-proposals"), proposal_payload(), format="json"
            )
        api_client.force_authenticate(user=admin_user)

        response = api_client.get(reverse("campaign-proposals"))

        assert response.status_code == 200
        assert response.data["count"] == 2

    def test_user_sees_only_own_proposals(
        self, api_client: APIClient, user: User, other_user: User
    ) -> None:
        api_client.force_authenticate(user=other_user)
        api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )
        api_client.force_authenticate(user=user)
        api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )

        response = api_client.get(reverse("campaign-proposals"))

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["creator"] == user.pk

    def test_filter_by_approval_status(
        self, api_client: APIClient, user: User, admin_user: User
    ) -> None:
        api_client.force_authenticate(user=user)
        pending_response = api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )
        approved_response = api_client.post(
            reverse("campaign-proposals"),
            proposal_payload(title="Second proposal"),
            format="json",
        )
        api_client.force_authenticate(user=admin_user)
        api_client.post(
            reverse(
                "campaign-proposal-approve",
                kwargs={"campaign_id": approved_response.data["id"]},
            )
        )

        response = api_client.get(
            reverse("campaign-proposals"), {"approval_status": "PENDING"}
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == pending_response.data["id"]


@pytest.mark.django_db
class TestApproveProposal:
    def test_approve_sets_status_and_reviewer(
        self, api_client: APIClient, user: User, admin_user: User
    ) -> None:
        api_client.force_authenticate(user=user)
        proposal_response = api_client.post(
            reverse("campaign-proposals"), proposal_payload(), format="json"
        )
        campaign_id = proposal_response.data["id"]
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-approve", kwargs={"campaign_id": campaign_id})
        )

        assert response.status_code == 200, response.data
        campaign = Campaign.objects.get(pk=campaign_id)
        assert campaign.approval_status == Campaign.ApprovalStatus.APPROVED
        assert campaign.reviewed_by_id == admin_user.pk
        assert campaign.reviewed_at is not None
        assert campaign.status == Campaign.Status.PROMOTION

        list_response = api_client.get(reverse("campaign-list"))
        assert any(
            campaign["id"] == campaign_id for campaign in list_response.data["results"]
        )

    def test_approve_non_pending_returns_400(
        self, api_client: APIClient, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.APPROVED)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-approve", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400

    def test_approve_expired_end_date_returns_400(
        self, api_client: APIClient, admin_user: User, campaign_factory: Callable
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            approval_status=Campaign.ApprovalStatus.PENDING,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-approve", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 400

    def test_approve_by_non_admin_returns_403(
        self, api_client: APIClient, user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-proposal-approve", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestRejectProposal:
    def test_reject_with_reason(
        self, api_client: APIClient, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-reject", kwargs={"campaign_id": campaign.pk}),
            {"rejection_reason": "Duplicate of another campaign."},
            format="json",
        )

        assert response.status_code == 200, response.data
        campaign.refresh_from_db()
        assert campaign.approval_status == Campaign.ApprovalStatus.REJECTED
        assert campaign.rejection_reason == "Duplicate of another campaign."
        assert campaign.reviewed_by_id == admin_user.pk
        assert campaign.reviewed_at is not None

    def test_reject_without_reason_returns_400(
        self, api_client: APIClient, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-reject", kwargs={"campaign_id": campaign.pk}),
            {"rejection_reason": ""},
            format="json",
        )

        assert response.status_code == 400

    def test_reject_non_pending_returns_400(
        self, api_client: APIClient, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.APPROVED)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-proposal-reject", kwargs={"campaign_id": campaign.pk}),
            {"rejection_reason": "Too late."},
            format="json",
        )

        assert response.status_code == 400

    def test_reject_by_non_admin_returns_403(
        self, api_client: APIClient, user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-proposal-reject", kwargs={"campaign_id": campaign.pk}),
            {"rejection_reason": "Not allowed."},
            format="json",
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestSyncExcludesPendingProposals:
    def test_pending_proposal_with_past_dates_is_untouched(
        self, campaign_factory: Callable
    ) -> None:
        now = timezone.now()
        campaign = campaign_factory(
            approval_status=Campaign.ApprovalStatus.PENDING,
            status=Campaign.Status.PROMOTION,
            start_date=now - 2 * DAY,
            end_date=now - DAY,
        )

        assert sync_campaign_statuses(now) == 0

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.PROMOTION
        assert campaign.approval_status == Campaign.ApprovalStatus.PENDING


@pytest.mark.django_db
class TestApprovalConstraints:
    def test_private_campaign_must_be_approved(self, user: User, clan: Clan) -> None:
        with pytest.raises(IntegrityError):
            Campaign.objects.create(
                **campaign_data(
                    creator=user,
                    scope=Campaign.Scope.PRIVATE,
                    target_clan=clan,
                    approval_status=Campaign.ApprovalStatus.PENDING,
                )
            )

    def test_rejected_requires_reason(self, user: User) -> None:
        with pytest.raises(IntegrityError):
            Campaign.objects.create(
                **campaign_data(
                    creator=user,
                    approval_status=Campaign.ApprovalStatus.REJECTED,
                    rejection_reason="",
                )
            )


@pytest.mark.django_db
class TestServiceHelpersDirectly:
    def test_approve_campaign_helper(
        self, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)

        approved = approve_campaign(admin_user, campaign.pk)

        assert approved.approval_status == Campaign.ApprovalStatus.APPROVED
        assert approved.reviewed_by_id == admin_user.pk

    def test_reject_campaign_helper(
        self, admin_user: User, campaign_factory: Callable
    ) -> None:
        campaign = campaign_factory(approval_status=Campaign.ApprovalStatus.PENDING)

        rejected = reject_campaign(admin_user, campaign.pk, "Not aligned.")

        assert rejected.approval_status == Campaign.ApprovalStatus.REJECTED
        assert rejected.rejection_reason == "Not aligned."
