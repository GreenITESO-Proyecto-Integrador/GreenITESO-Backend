"""Tests for the GET /api/v1/action-logs/mine/ endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import User, UserProfile
from green_iteso.actions.models import ActionLog
from green_iteso.actions.tests.helpers import create_bike_action, create_student


class ActionLogMineViewTest(TestCase):
    """Tests for the authenticated user's own action log history."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.user = create_student()
        self.clan = self.user.profile.institutional_clan
        self.client.force_authenticate(user=self.user)
        self.other_user = User.objects.create_user(
            email="other@iteso.mx", password="local-only"
        )
        UserProfile.objects.create(user=self.other_user, institutional_clan=self.clan)

    def test_mine_requires_authentication(self) -> None:
        """Anonymous caller gets 401."""
        self.client.logout()
        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_mine_returns_only_caller_logs(self) -> None:
        """Other users' logs are not included."""
        # Create a log for other user
        action = create_bike_action()
        clan = self.clan
        ActionLog.objects.create(
            user=self.other_user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
        )

        # Create a log for the authenticated user
        my_log = ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        self.assertEqual(len(logs_data), 1)
        self.assertEqual(uuid.UUID(logs_data[0]["id"]), my_log.id)

    def test_mine_returns_all_statuses_by_default(self) -> None:
        """APPROVED + PENDING_AUDIT + REJECTED all appear."""
        action = create_bike_action()
        clan = self.clan

        # Create logs in different statuses
        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
        )
        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.PENDING_AUDIT,
        )
        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.REJECTED,
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        logs_data = response.data["results"]

        # Should return all 3
        self.assertEqual(len(logs_data), 3)

        # Check that we have the correct statuses
        statuses = [log["status"] for log in logs_data]
        self.assertIn("APPROVED", statuses)
        self.assertIn("PENDING_AUDIT", statuses)
        self.assertIn("REJECTED", statuses)

    def test_mine_filters_by_status(self) -> None:
        """Filtering by status works correctly."""
        action = create_bike_action()
        clan = self.clan

        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
        )
        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.PENDING_AUDIT,
        )
        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.REJECTED,
        )

        # Filter for approved
        response = self.client.get(f"{reverse('action-log-mine')}?status=APPROVED")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        self.assertEqual(len(logs_data), 1)
        self.assertEqual(logs_data[0]["status"], "APPROVED")

    def test_mine_rejects_invalid_status(self) -> None:
        """Invalid status returns 400."""
        response = self.client.get(f"{reverse('action-log-mine')}?status=WAITING")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mine_ordered_newest_first(self) -> None:
        """Results are ordered newest first by created_at."""
        action = create_bike_action()
        clan = self.clan

        # Create logs with different timestamps (older first)
        old_log = ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
            created_at=datetime(2021, 1, 1, tzinfo=UTC),
        )
        new_log = ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
            created_at=datetime(2023, 1, 1, tzinfo=UTC),
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        # Should return newest first
        self.assertEqual(uuid.UUID(logs_data[0]["id"]), new_log.id)
        self.assertEqual(uuid.UUID(logs_data[1]["id"]), old_log.id)

    def test_mine_paginated(self) -> None:
        """Response contains pagination fields."""
        action = create_bike_action()
        clan = self.clan

        # Create more logs than page size
        for _ in range(60):  # 60 logs should be more than default 50
            ActionLog.objects.create(
                user=self.user,
                action=action,
                institutional_clan=clan,
                idempotency_key=str(uuid.uuid4()),
                points_awarded=50,
                status=ActionLog.Status.APPROVED,
            )

        response = self.client.get(reverse("action-log-mine"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertIn("next", response.data)

    def test_mine_serializer_fields(self) -> None:
        """Spot-check the serializer fields."""
        action = create_bike_action()
        clan = self.clan

        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        self.assertEqual(len(logs_data), 1)

        log_data = logs_data[0]
        # Check key fields are present
        self.assertIn("id", log_data)
        self.assertIn("action", log_data)
        self.assertIn("status", log_data)
        self.assertIn("points_awarded", log_data)
        self.assertIn("has_evidence", log_data)
        self.assertIn("rejection_reason", log_data)
        self.assertIn("created_at", log_data)

    def test_mine_has_evidence_true_when_key_present(self) -> None:
        """has_evidence returns True when evidence_object_key is present."""
        action = create_bike_action()
        clan = self.clan

        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
            evidence_object_key="some-key",
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        self.assertTrue(logs_data[0]["has_evidence"])

    def test_mine_has_evidence_false_when_key_absent(self) -> None:
        """has_evidence returns False when evidence_object_key is blank."""
        action = create_bike_action()
        clan = self.clan

        ActionLog.objects.create(
            user=self.user,
            action=action,
            institutional_clan=clan,
            idempotency_key=str(uuid.uuid4()),
            points_awarded=50,
            status=ActionLog.Status.APPROVED,
            evidence_object_key="",
        )

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        logs_data = response.data["results"]
        self.assertFalse(logs_data[0]["has_evidence"])
