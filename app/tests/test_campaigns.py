"""Unit and integration tests for the campaigns API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)
from green_iteso.campaigns.serializers import (
    CampaignSerializer,
    MissionSerializer,
    UserMissionProgressSerializer,
)


@dataclass
class _FakeRequest:
    user: User


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="student@example.test", password="test-pass")


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user(email="other@example.test", password="test-pass")


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_user(
        email="admin@example.test", password="test-pass", role=User.Role.ADMIN
    )


@pytest.fixture
def clan(user: User) -> Clan:
    return Clan.objects.create(
        name="Test clan",
        type=Clan.ClanType.PRIVATE,
        created_by=user,
    )


@pytest.fixture
def action_category() -> ActionCategory:
    return ActionCategory.objects.create(
        code="WASTE",
        name="Waste",
        description="Waste actions",
    )


@pytest.fixture
def action(action_category: ActionCategory) -> ActionMaster:
    return ActionMaster.objects.create(
        code="RECYCLE",
        category=action_category,
        name="Recycle",
        description="Recycle something",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )


def campaign_data(**overrides: Any) -> dict[str, Any]:
    current_time = timezone.now()
    data: dict[str, Any] = {
        "title": "Test campaign",
        "description": "Campaign description",
        "scope": Campaign.Scope.GLOBAL,
        "status": Campaign.Status.PROMOTION,
        "start_date": current_time,
        "end_date": current_time + timedelta(days=7),
    }
    data.update(overrides)
    return data


@pytest.fixture
def campaign_factory(user: User) -> Callable[..., Campaign]:
    def create_campaign(**overrides: Any) -> Campaign:
        values = campaign_data(creator=user, **overrides)
        return Campaign.objects.create(**values)

    return create_campaign


@pytest.fixture
def campaign(campaign_factory: Any) -> Campaign:
    return campaign_factory()


@pytest.fixture
def mission(campaign: Campaign, action: ActionMaster) -> Mission:
    return Mission.objects.create(campaign=campaign, action=action, target_count=5)


@pytest.mark.django_db
class TestCampaignSerializer:
    def test_global_campaign_without_clan_is_valid(self, user: User) -> None:
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
            data=campaign_data(), context={"request": _FakeRequest(user=user)}
        )

        with pytest.raises(PermissionDenied):
            serializer.is_valid(raise_exception=True)

    def test_global_scope_allowed_for_admin(self, admin_user: User) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(), context={"request": _FakeRequest(user=admin_user)}
        )

        assert serializer.is_valid(), serializer.errors

    def test_private_scope_requires_clan_leader(self, user: User, clan: Clan) -> None:
        serializer = CampaignSerializer(
            data=campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=clan.pk),
            context={"request": _FakeRequest(user=user)},
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
            context={"request": _FakeRequest(user=user)},
        )

        assert serializer.is_valid(), serializer.errors

    def test_podium_snapshot_is_not_serialized(self, campaign: Campaign) -> None:
        campaign.podium_snapshot = {"winner": "hidden"}
        campaign.save(update_fields=["podium_snapshot"])

        assert "podium_snapshot" not in CampaignSerializer(campaign).data


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
class TestUserMissionProgressSerializer:
    def test_progress_percentage_is_calculated(
        self, user: User, mission: Mission
    ) -> None:
        progress = UserMissionProgress.objects.create(
            user=user, mission=mission, current_count=2
        )

        assert (
            UserMissionProgressSerializer(progress).data["progress_percentage"] == 40.0
        )

    def test_zero_target_returns_zero_percentage(
        self, user: User, mission: Mission
    ) -> None:
        mission.target_count = 0

        progress = UserMissionProgress(user=user, mission=mission, current_count=0)

        assert (
            UserMissionProgressSerializer(progress).data["progress_percentage"] == 0.0
        )

    def test_is_completed_is_read_only(self, user: User, mission: Mission) -> None:
        progress = UserMissionProgress.objects.create(user=user, mission=mission)
        serializer = UserMissionProgressSerializer(
            progress, data={"is_completed": True}, partial=True
        )

        assert serializer.is_valid(), serializer.errors
        assert "is_completed" not in serializer.validated_data


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
        Campaign.objects.create(
            **campaign_data(
                title="global", creator=user, status=Campaign.Status.PROMOTION
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
        assert (
            with_progress.data["results"][0]["user_mission_progress"][0][
                "current_count"
            ]
            == 2
        )

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
        data = campaign_data(scope=scope)
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

    def test_create_without_missions_succeeds(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            reverse("campaign-list"), campaign_data(), format="json"
        )

        assert response.status_code == 201
        assert Mission.objects.count() == 0

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
            campaign_data(scope=Campaign.Scope.PRIVATE, target_clan=str(clan.pk)),
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
        CampaignParticipant.objects.create(campaign=campaign, user=user)
        UserMissionProgress.objects.create(user=user, mission=mission, current_count=1)
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("campaign-detail", kwargs={"campaign_id": campaign.pk})
        )

        assert response.status_code == 200
        assert len(response.data["missions"]) == 1
        assert len(response.data["participants"]) == 1
        assert response.data["user_mission_progress"][0]["current_count"] == 1

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
        assert empty_response.data == []

        CampaignParticipant.objects.create(campaign=campaign, user=user)
        full_response = api_client.get(url)
        assert full_response.status_code == 200
        assert len(full_response.data) == 1


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


@pytest.mark.django_db
class TestMissionProgressEndpoint:
    def test_get_creates_zero_progress_and_returns_existing(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        first_response = api_client.get(url)
        second_response = api_client.get(url)

        assert first_response.status_code == 200
        assert first_response.data["current_count"] == 0
        assert second_response.data["current_count"] == 0
        assert (
            UserMissionProgress.objects.filter(user=user, mission=mission).count() == 1
        )

    def test_get_missing_mission_is_404(
        self, api_client: APIClient, user: User
    ) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse(
                "mission-progress",
                kwargs={"mission_id": "00000000-0000-0000-0000-000000000000"},
            )
        )

        assert response.status_code == 404

    def test_increment_updates_progress(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, {"increment": 2}, format="json")

        assert response.status_code == 200
        assert response.data["current_count"] == 2

    def test_reaching_target_marks_progress_completed(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, {"current_count": 5}, format="json")

        assert response.status_code == 200
        assert response.data["is_completed"] is True

    def test_exceeding_target_is_rejected_and_not_saved(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, {"increment": 6}, format="json")

        assert response.status_code == 400
        assert (
            UserMissionProgress.objects.get(user=user, mission=mission).current_count
            == 0
        )

    def test_current_count_without_increment_updates_progress(
        self, api_client: APIClient, user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, {"current_count": 3}, format="json")

        assert response.status_code == 200
        assert response.data["current_count"] == 3

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (
                {"increment": 1, "current_count": 1},
                "Send either increment or current_count, not both.",
            ),
            ({"increment": "not-an-integer"}, "Increment must be an integer."),
        ],
    )
    def test_invalid_increment_payload_returns_400(
        self,
        api_client: APIClient,
        user: User,
        mission: Mission,
        payload: dict[str, Any],
        message: str,
    ) -> None:
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, payload, format="json")

        assert response.status_code == 400
        assert message in str(response.data)

    def test_user_cannot_modify_another_users_progress(
        self,
        api_client: APIClient,
        user: User,
        other_user: User,
        mission: Mission,
    ) -> None:
        UserMissionProgress.objects.create(
            user=other_user, mission=mission, current_count=1
        )
        api_client.force_authenticate(user=user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        response = api_client.patch(url, {"increment": 2}, format="json")

        assert response.status_code == 200
        assert response.data["current_count"] == 2
        assert (
            UserMissionProgress.objects.get(
                user=other_user, mission=mission
            ).current_count
            == 1
        )
