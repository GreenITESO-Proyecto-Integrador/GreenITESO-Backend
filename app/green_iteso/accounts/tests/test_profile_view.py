"""Coverage for GET /api/v1/profile/me/ (T2-20)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.campaigns.models import Campaign, CampaignParticipant

PROFILE_URL = "/api/v1/profile/me/"


@pytest.mark.django_db
def test_profile_requires_authentication() -> None:
    response = APIClient().get(PROFILE_URL)

    assert response.status_code == 401


@pytest.mark.django_db
def test_profile_returns_the_callers_own_data_with_placeholders() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(PROFILE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(caller.pk)
    assert body["email"] == "ana@iteso.mx"
    assert body["level"] is None
    assert body["badges"] == []
    assert body["institutional_clan"] is None
    assert body["active_private_clan"] is None
    assert body["impact_metrics"] == {
        "co2_kg": "0.000",
        "water_liters": "0.000",
        "plastic_kg": "0.000",
    }
    assert body["finished_campaigns"] == []


@pytest.mark.django_db
def test_profile_includes_institutional_and_active_private_clan() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    private_clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    UserProfile.objects.create(user=caller, institutional_clan=institutional_clan)
    ClanMembership.objects.create(
        user=caller, clan=private_clan, is_active_private=True
    )
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(PROFILE_URL)

    body = response.json()
    assert body["institutional_clan"] == {
        "id": str(institutional_clan.pk),
        "name": "Ingeniería de Software",
    }
    assert body["active_private_clan"] == {
        "id": str(private_clan.pk),
        "name": "Green Team",
    }


@pytest.mark.django_db
def test_profile_lists_only_finished_campaigns() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    now = timezone.now()
    finished = Campaign.objects.create(
        title="Semana Verde",
        scope=Campaign.Scope.GLOBAL,
        status=Campaign.Status.FINISHED,
        creator=caller,
        start_date=now,
        end_date=now + timedelta(days=1),
    )
    Campaign.objects.create(
        title="Reto Reciclaje",
        scope=Campaign.Scope.GLOBAL,
        status=Campaign.Status.IN_PROGRESS,
        creator=caller,
        start_date=now,
        end_date=now + timedelta(days=1),
    )
    CampaignParticipant.objects.create(campaign=finished, user=caller)
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get(PROFILE_URL)

    titles = [row["title"] for row in response.json()["finished_campaigns"]]
    assert titles == ["Semana Verde"]
