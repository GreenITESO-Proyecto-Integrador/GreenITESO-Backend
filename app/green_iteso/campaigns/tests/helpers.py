"""Shared builders for the campaigns tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.utils import timezone

from green_iteso.accounts.models import User
from green_iteso.campaigns.models import Campaign


@dataclass
class FakeRequest:
    user: User


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
