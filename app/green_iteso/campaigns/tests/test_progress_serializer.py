"""Tests for UserMissionProgressSerializer."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import (
    Mission,
    UserMissionProgress,
)
from green_iteso.campaigns.serializers import (
    UserMissionProgressSerializer,
)


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
