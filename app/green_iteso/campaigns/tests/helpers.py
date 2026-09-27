"""Shared builders for the campaigns tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import Campaign


@dataclass
class FakeRequest:
    user: User


def assert_join_rejected_inactive(api_client: APIClient, campaign: Campaign) -> None:
    """Post a join request for ``campaign`` and assert it is rejected as inactive."""
    response = api_client.post(
        reverse("campaign-join", kwargs={"campaign_id": campaign.pk})
    )

    assert response.status_code == 400
    assert response.data["detail"] == "Campaign is not active."


def campaign_data(**overrides: Any) -> dict[str, Any]:
    current_time = timezone.now()
    data: dict[str, Any] = {
        "title": "Test campaign",
        "description": "Campaign description",
        "scope": Campaign.Scope.GLOBAL,
        "status": Campaign.Status.PROMOTION,
        "start_date": current_time + timedelta(hours=1),
        "end_date": current_time + timedelta(days=7),
    }
    data.update(overrides)
    return data
