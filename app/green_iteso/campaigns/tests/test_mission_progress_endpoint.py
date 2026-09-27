"""Tests for the mission progress endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import (
    Mission,
    UserMissionProgress,
)


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

    def test_progress_rejected_for_non_participant(
        self, api_client: APIClient, other_user: User, mission: Mission
    ) -> None:
        api_client.force_authenticate(user=other_user)
        url = reverse("mission-progress", kwargs={"mission_id": mission.pk})

        get_response = api_client.get(url)
        patch_response = api_client.patch(url, {"increment": 1}, format="json")

        assert get_response.status_code == 403
        assert patch_response.status_code == 403
        assert not UserMissionProgress.objects.filter(
            user=other_user, mission=mission
        ).exists()

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
