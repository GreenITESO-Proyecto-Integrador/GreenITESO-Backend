"""Tests for the campaign list, create and detail endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)
from green_iteso.campaigns.tests.helpers import campaign_data, default_missions


@pytest.mark.django_db
class TestCampaignEndpoints:
    def test_campaign_list_is_paginated_and_ordered(
        self, api_client: APIClient, user: User
    ) -> None:
        for index in range(2):
            Campaign.objects.create(
                **campaign_data(
                    title=f"Campaign {index}",
                    creator=user,
                    start_date=timezone.now() + timedelta(days=index),
                )
            )
        api_client.force_authenticate(user=user)

        response = api_client.get(reverse("campaign-list"))

        assert response.status_code == 200
        assert set(response.data) == {"count", "next", "previous", "results"}
        assert [item["title"] for item in response.data["results"]] == [
            "Campaign 1",
            "Campaign 0",
        ]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ({"scope": "PRIVATE"}, {"private-active"}),
            ({"scope__in": "GLOBAL,PRIVATE"}, {"global", "private-active", "finished"}),
            ({"status": "FINISHED"}, {"finished"}),
            ({"is_active": "true"}, {"global", "private-active"}),
            ({"scope": "PRIVATE", "status": "IN_PROGRESS"}, {"private-active"}),
        ],
    )
    def test_campaign_filters_work_independently_and_combined(
        self,
        api_client: APIClient,
        user: User,
        clan: Clan,
        query: dict[str, str],
        expected: set[str],
    ) -> None:
        current_time = timezone.now()
        ClanMembership.objects.create(user=user, clan=clan)
        Campaign.objects.create(
            **campaign_data(
                title="global",
                creator=user,
                status=Campaign.Status.PROMOTION,
                start_date=current_time,
            )
        )
        Campaign.objects.create(
            **campaign_data(
                title="private-active",
                creator=user,
                scope=Campaign.Scope.PRIVATE,
                target_clan=clan,
                status=Campaign.Status.IN_PROGRESS,
            )
        )
        Campaign.objects.create(
            **campaign_data(
                title="finished",
                creator=user,
                status=Campaign.Status.FINISHED,
                start_date=current_time - timedelta(days=3),
                end_date=current_time - timedelta(days=1),
            )
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(reverse("campaign-list"), query)

        assert response.status_code == 200
        assert {item["title"] for item in response.data["results"]} == expected

    def test_list_includes_existing_user_progress_and_empty_when_absent(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("campaign-list")

        without_progress = api_client.get(url)
        assert without_progress.data["results"][0]["user_mission_progress"] == []

        UserMissionProgress.objects.create(user=user, mission=mission, current_count=2)
        with_progress = api_client.get(url)
        progress_entry = with_progress.data["results"][0]["user_mission_progress"][0]
        assert progress_entry["current_count"] == 2
        assert progress_entry["mission"] == mission.pk

    def test_unauthenticated_list_is_rejected(self, api_client: APIClient) -> None:
        response = api_client.get(reverse("campaign-list"))

        assert response.status_code in {401, 403}

    @pytest.mark.parametrize("scope", [Campaign.Scope.GLOBAL, Campaign.Scope.PRIVATE])
    def test_create_uses_authenticated_creator(
        self,
        api_client: APIClient,
        user: User,
        admin_user: User,
        clan: Clan,
        scope: str,
    ) -> None:
        data = campaign_data(scope=scope, missions=default_missions())
        if scope == Campaign.Scope.PRIVATE:
            data["target_clan"] = str(clan.pk)
            ClanMembership.objects.create(
                user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
            )
            creator = user
        else:
            creator = admin_user
        data["creator"] = "00000000-0000-0000-0000-000000000000"
        api_client.force_authenticate(user=creator)

        response = api_client.post(reverse("campaign-list"), data, format="json")

        assert response.status_code == 201
        assert response.data["creator"] == creator.pk
        assert Campaign.objects.get(pk=response.data["id"]).creator_id == creator.pk

    @pytest.mark.parametrize(
        "data",
        [
            campaign_data(
                start_date=timezone.now() + timedelta(days=1),
                end_date=timezone.now(),
            ),
            campaign_data(scope=Campaign.Scope.PRIVATE),
        ],
    )
    def test_invalid_create_returns_400_without_creating_campaign(
        self, api_client: APIClient, user: User, data: dict[str, Any]
    ) -> None:
        api_client.force_authenticate(user=user)
        before = Campaign.objects.count()

        response = api_client.post(reverse("campaign-list"), data, format="json")

        assert response.status_code == 400
        assert Campaign.objects.count() == before

    @pytest.mark.parametrize("missions", [None, []])
    def test_create_without_missions_is_rejected(
        self, api_client: APIClient, admin_user: User, missions: list | None
    ) -> None:
        api_client.force_authenticate(user=admin_user)
        data = campaign_data()
        if missions is not None:
            data["missions"] = missions

        response = api_client.post(reverse("campaign-list"), data, format="json")

        assert response.status_code == 400
        assert "missions" in response.data
        assert Campaign.objects.count() == 0

    @pytest.mark.parametrize("role", [User.Role.STUDENT, User.Role.STAFF])
    def test_global_create_rejects_non_admin(
        self, api_client: APIClient, role: str
    ) -> None:
        non_admin = User.objects.create_user(
            email=f"{role.lower()}@example.test", password="test-pass", role=role
        )
        api_client.force_authenticate(user=non_admin)

        response = api_client.post(
            reverse("campaign-list"), campaign_data(), format="json"
        )

        assert response.status_code == 403
        assert Campaign.objects.count() == 0

    def test_private_create_allowed_for_clan_leader(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(
                scope=Campaign.Scope.PRIVATE,
                target_clan=str(clan.pk),
                missions=default_missions(),
            ),
            format="json",
        )

        assert response.status_code == 201

    def test_private_create_rejected_for_clan_member(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=str(clan.pk)),
            format="json",
        )

        assert response.status_code == 403
        assert Campaign.objects.count() == 0

    def test_private_create_rejected_without_membership(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=str(clan.pk)),
            format="json",
        )

        assert response.status_code == 403
        assert Campaign.objects.count() == 0

    def test_unauthenticated_create_is_rejected(self, api_client: APIClient) -> None:
        response = api_client.post(
            reverse("campaign-list"), campaign_data(), format="json"
        )

        assert response.status_code in {401, 403}

    def test_campaign_detail_contains_related_data(
        self, api_client: APIClient, user: User, campaign: Campaign, mission: Mission
    ) -> None:
        UserMissionProgress.objects.create(user=user, mission=mission, current_count=1)
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 200
        assert len(response.data["missions"]) == 1
        assert len(response.data["participants"]) == 1
        assert response.data["user_mission_progress"][0]["current_count"] == 1
        assert response.data["user_mission_progress"][0]["mission"] == mission.pk

    def test_missing_campaign_detail_is_404(
        self, api_client: APIClient, user: User
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse(
                "campaign-detail",
                kwargs={"campaign_id": "00000000-0000-0000-0000-000000000000"},
            )
        )

        assert response.status_code == 404

    def test_participant_list_returns_full_or_empty_list(
        self, api_client: APIClient, user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("campaign-participants", kwargs={"campaign_id": campaign.pk})

        empty_response = api_client.get(url)
        assert empty_response.status_code == 200
        assert empty_response.data["results"] == []

        CampaignParticipant.objects.create(campaign=campaign, user=user)
        full_response = api_client.get(url)
        assert full_response.status_code == 200
        assert len(full_response.data["results"]) == 1

    def test_private_campaign_hidden_from_non_member_list(
        self, api_client: APIClient, user: User, other_user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(user=user, clan=clan)
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
            )
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.get(reverse("campaign-list"))

        assert response.status_code == 200
        assert campaign.pk not in [item["id"] for item in response.data["results"]]

    def test_private_campaign_detail_is_404_for_non_member(
        self, api_client: APIClient, user: User, other_user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(user=user, clan=clan)
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
            )
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 404

    def test_private_campaign_participants_404_for_non_member(
        self, api_client: APIClient, user: User, other_user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(user=user, clan=clan)
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
            )
        )
        api_client.force_authenticate(user=other_user)

        response = api_client.get(
            reverse("campaign-participants", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 404

    def test_private_campaign_visible_to_clan_member(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(user=user, clan=clan)
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
            )
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestPrivateCampaignCreationRules:
    def test_private_campaign_to_institutional_clan_returns_400(
        self, api_client: APIClient, user: User
    ) -> None:
        institutional = Clan.objects.create(
            name="Institutional clan",
            type=Clan.ClanType.INSTITUTIONAL,
            created_by=user,
        )
        ClanMembership.objects.create(
            user=user, clan=institutional, role=ClanMembership.MembershipRole.LEADER
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(
                scope=Campaign.Scope.PRIVATE, target_clan=str(institutional.pk)
            ),
            format="json",
        )

        assert response.status_code == 400
        assert "target_clan" in response.data
        assert Campaign.objects.count() == 0

    def test_private_campaign_to_deleted_clan_returns_400(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        Clan.all_objects.filter(pk=clan.pk).update(deleted_at=timezone.now())
        api_client.force_authenticate(user=user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=str(clan.pk)),
            format="json",
        )

        assert response.status_code == 400
        assert "target_clan" in response.data
        assert Campaign.objects.count() == 0

    def test_admin_not_leader_cannot_create_private_campaign(
        self, api_client: APIClient, admin_user: User, clan: Clan
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"),
            campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=str(clan.pk)),
            format="json",
        )

        assert response.status_code == 403
        assert Campaign.objects.count() == 0


@pytest.mark.django_db
class TestCampaignParticipatingFilterAndIsParticipant:
    def test_participating_filter_returns_only_enrolled_campaigns_combined_with_status(
        self, api_client: APIClient, user: User
    ) -> None:
        joined_in_progress = Campaign.objects.create(
            **campaign_data(
                title="joined-in-progress",
                creator=user,
                status=Campaign.Status.IN_PROGRESS,
            )
        )
        CampaignParticipant.objects.create(campaign=joined_in_progress, user=user)
        joined_finished = Campaign.objects.create(
            **campaign_data(
                title="joined-finished",
                creator=user,
                status=Campaign.Status.FINISHED,
                start_date=timezone.now() - timedelta(days=3),
                end_date=timezone.now() - timedelta(days=1),
            )
        )
        CampaignParticipant.objects.create(campaign=joined_finished, user=user)
        Campaign.objects.create(
            **campaign_data(
                title="not-joined",
                creator=user,
                status=Campaign.Status.IN_PROGRESS,
                start_date=timezone.now() + timedelta(days=1),
            )
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("campaign-list"),
            {"participating": "true", "status": "IN_PROGRESS"},
        )

        assert response.status_code == 200
        assert {item["title"] for item in response.data["results"]} == {
            "joined-in-progress"
        }

    def test_is_participant_is_correct_in_list_and_detail(
        self, api_client: APIClient, user: User, other_user: User, campaign: Campaign
    ) -> None:
        CampaignParticipant.objects.create(campaign=campaign, user=user)
        api_client.force_authenticate(user=user)

        list_response = api_client.get(reverse("campaign-list"))
        detail_response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )

        assert list_response.data["results"][0]["is_participant"] is True
        assert detail_response.data["is_participant"] is True

        api_client.force_authenticate(user=other_user)
        list_as_other = api_client.get(reverse("campaign-list"))
        assert list_as_other.data["results"][0]["is_participant"] is False


@pytest.mark.django_db
class TestCanManageField:
    def test_global_campaign_can_manage_true_for_admin_false_for_student(
        self, api_client: APIClient, user: User, admin_user: User, campaign: Campaign
    ) -> None:
        api_client.force_authenticate(user=admin_user)
        as_admin = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )
        assert as_admin.data["can_manage"] is True

        api_client.force_authenticate(user=user)
        as_student = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )
        assert as_student.data["can_manage"] is False

    def test_private_campaign_can_manage_true_for_leader_false_for_member(
        self, api_client: APIClient, user: User, other_user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        ClanMembership.objects.create(
            user=other_user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
        )
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
            )
        )
        url = reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})

        api_client.force_authenticate(user=user)
        assert api_client.get(url).data["can_manage"] is True

        api_client.force_authenticate(user=other_user)
        assert api_client.get(url).data["can_manage"] is False

    def test_institutional_private_campaign_can_manage_admin_only(
        self, api_client: APIClient, user: User, admin_user: User
    ) -> None:
        institutional = Clan.objects.create(
            name="Institutional clan",
            type=Clan.ClanType.INSTITUTIONAL,
            created_by=user,
        )
        ClanMembership.objects.create(
            user=user, clan=institutional, role=ClanMembership.MembershipRole.LEADER
        )
        campaign = Campaign.objects.create(
            **campaign_data(
                creator=user,
                scope=Campaign.Scope.PRIVATE,
                target_clan=institutional,
            )
        )
        url = reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})

        api_client.force_authenticate(user=admin_user)
        assert api_client.get(url).data["can_manage"] is True

        other_clan = Clan.objects.create(
            name="Other clan", type=Clan.ClanType.PRIVATE, created_by=user
        )
        other_private = Campaign.objects.create(
            **campaign_data(
                creator=user,
                scope=Campaign.Scope.PRIVATE,
                target_clan=other_clan,
            )
        )
        other_url = reverse("campaign-detail", kwargs={"campaign_id": other_private.pk})
        assert api_client.get(other_url).data["can_manage"] is False

    def test_can_manage_present_in_list_and_create_responses(
        self, api_client: APIClient, user: User, clan: Clan
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )
        api_client.force_authenticate(user=user)

        create_response = api_client.post(
            reverse("campaign-list"),
            campaign_data(
                scope=Campaign.Scope.PRIVATE,
                target_clan=str(clan.pk),
                missions=default_missions(),
            ),
            format="json",
        )
        assert create_response.status_code == 201
        assert create_response.data["can_manage"] is True

        list_response = api_client.get(reverse("campaign-list"))
        assert list_response.data["results"][0]["can_manage"] is True

    def test_can_manage_query_count_does_not_grow_with_campaign_count(
        self, api_client: APIClient, user: User, django_assert_max_num_queries: Any
    ) -> None:
        clans = [
            Clan.objects.create(
                name=f"Clan {index}", type=Clan.ClanType.PRIVATE, created_by=user
            )
            for index in range(5)
        ]
        for clan in clans:
            ClanMembership.objects.create(
                user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
            )
            Campaign.objects.create(
                **campaign_data(
                    creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan
                )
            )
        api_client.force_authenticate(user=user)

        with django_assert_max_num_queries(20):
            response = api_client.get(reverse("campaign-list"))

        assert response.status_code == 200
        assert len(response.data["results"]) == 5
        assert all(item["can_manage"] is True for item in response.data["results"])
