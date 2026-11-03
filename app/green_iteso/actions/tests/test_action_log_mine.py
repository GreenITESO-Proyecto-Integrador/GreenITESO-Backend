"""Tests for the GET /api/v1/action-logs/mine/ endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.accounts.tests.factories import UserFactory
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.actions.selectors import list_my_action_logs
from green_iteso.actions.tests.factories import (
    ActionCategoryFactory,
    ActionLogFactory,
    ActionMasterFactory,
)


class ActionLogMineViewTest(TestCase):
    """Tests for the authenticated user's own action log history."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.user = UserFactory.create()
        self.client.force_authenticate(user=self.user)
        self.other_user = UserFactory.create()

    def test_mine_requires_authentication(self) -> None:
        """Anonymous caller gets 401."""
        self.client.logout()
        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_mine_returns_only_caller_logs(self) -> None:
        """Other users' logs are not included."""
        # Create a log for other user
        ActionLogFactory.create(user=self.other_user)
        
        # Create a log for the authenticated user 
        my_log = ActionLogFactory.create(user=self.user)

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        logs_data = response.data["results"]
        self.assertEqual(len(logs_data), 1)
        self.assertEqual(uuid.UUID(logs_data[0]["id"]), my_log.id)

    def test_mine_returns_all_statuses_by_default(self) -> None:
        """APPROVED + PENDING_AUDIT + REJECTED all appear."""
        # Create logs in different statuses
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.APPROVED)
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.PENDING_AUDIT)
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.REJECTED)
        
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
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.APPROVED)
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.PENDING_AUDIT)
        ActionLogFactory.create(user=self.user, status=ActionLog.Status.REJECTED)

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
        # Create logs with different timestamps (older first)
        old_log = ActionLogFactory.create(user=self.user, created_at=datetime(2021, 1, 1, tzinfo=UTC))
        new_log = ActionLogFactory.create(user=self.user, created_at=datetime(2023, 1, 1, tzinfo=UTC))

        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        logs_data = response.data["results"]
        # Should return newest first
        self.assertEqual(uuid.UUID(logs_data[0]["id"]), new_log.id)
        self.assertEqual(uuid.UUID(logs_data[1]["id"]), old_log.id)

    def test_mine_paginated(self) -> None:
        """Response contains pagination fields."""
        # Create more logs than page size
        for _ in range(60):  # 60 logs should be more than default 50
            ActionLogFactory.create(user=self.user)
            
        response = self.client.get(reverse("action-log-mine"))
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertIn("next", response.data)

    def test_mine_serializer_fields(self) -> None:
        """Spot-check the serializer fields."""
        log = ActionLogFactory.create(user=self.user)
        
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
        log = ActionLogFactory.create(user=self.user, evidence_object_key="some-key")
        
        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        logs_data = response.data["results"]
        self.assertTrue(logs_data[0]["has_evidence"])

    def test_mine_has_evidence_false_when_key_absent(self) -> None:
        """has_evidence returns False when evidence_object_key is blank."""
        log = ActionLogFactory.create(user=self.user, evidence_object_key="")
        
        response = self.client.get(reverse("action-log-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        logs_data = response.data["results"]
        self.assertFalse(logs_data[0]["has_evidence"])