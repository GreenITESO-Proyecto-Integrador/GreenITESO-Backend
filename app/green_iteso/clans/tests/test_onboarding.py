"""Coverage for the first-login onboarding modal's endpoints (#163)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.clans.services import complete_onboarding

ONBOARDING_URL = "/api/v1/clans/institutional-clan/"
CAREERS_URL = "/api/v1/clans/institutional-clan/careers/"


def _client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_careers_lists_alive_institutional_clans_sorted() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    Clan.objects.create(name="Ingeniería en Sistemas", type=Clan.ClanType.INSTITUTIONAL)
    Clan.objects.create(name="Arquitectura", type=Clan.ClanType.INSTITUTIONAL)
    Clan.objects.create(name="Clan privado", type=Clan.ClanType.PRIVATE)

    response = _client_for(caller).get(CAREERS_URL)

    assert response.status_code == 200
    assert response.json() == {"careers": ["Arquitectura", "Ingeniería en Sistemas"]}


@pytest.mark.django_db
def test_careers_requires_authentication() -> None:
    assert APIClient().get(CAREERS_URL).status_code == 401


@pytest.mark.django_db
def test_onboarding_sets_nickname_and_visibility_with_the_career() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    response = _client_for(caller).post(
        ONBOARDING_URL,
        {"career": "Arquitectura", "nickname": " Ana ", "visibility": "PRIVATE"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["institutional_clan"]["name"] == "Arquitectura"
    assert body["nickname"] == "Ana"
    assert body["visibility"] == "PRIVATE"
    caller.refresh_from_db()
    assert caller.nickname == "Ana"
    assert UserProfile.objects.get(user=caller).visibility == "PRIVATE"
    assert ClanMembership.objects.filter(
        user=caller, clan__name="Arquitectura"
    ).exists()


@pytest.mark.django_db
def test_onboarding_keeps_an_existing_nickname() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    caller.nickname = "Original"
    caller.save(update_fields=["nickname"])

    response = _client_for(caller).post(
        ONBOARDING_URL, {"career": "Arquitectura", "nickname": "Otro"}
    )

    assert response.status_code == 200
    assert response.json()["nickname"] == "Original"
    caller.refresh_from_db()
    assert caller.nickname == "Original"


@pytest.mark.django_db
def test_onboarding_without_optional_fields_only_assigns_the_clan() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    response = _client_for(caller).post(ONBOARDING_URL, {"career": "Arquitectura"})

    assert response.status_code == 200
    caller.refresh_from_db()
    assert caller.nickname == ""
    assert UserProfile.objects.get(user=caller).visibility == "PUBLIC"


@pytest.mark.django_db
def test_onboarding_rejects_an_unknown_visibility() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    response = _client_for(caller).post(
        ONBOARDING_URL, {"career": "Arquitectura", "visibility": "FRIENDS"}
    )

    assert response.status_code == 400
    assert not ClanMembership.objects.filter(user=caller).exists()


@pytest.mark.django_db
def test_complete_onboarding_rolls_back_the_nickname_when_the_career_fails() -> None:
    # The nickname is written before the clan assignment, so this only passes
    # if both run in one transaction.
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    Clan.objects.create(name="Ocupado", type=Clan.ClanType.PRIVATE)

    with pytest.raises(ValueError):
        complete_onboarding(user=caller, career="Ocupado", nickname="Ana")

    caller.refresh_from_db()
    assert caller.nickname == ""


@pytest.mark.django_db
def test_complete_onboarding_rolls_back_when_the_profile_save_fails() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    with pytest.raises(IntegrityError):
        complete_onboarding(
            user=caller, career="Arquitectura", nickname="Ana", visibility="HIDDEN"
        )

    caller.refresh_from_db()
    assert caller.nickname == ""
    assert not ClanMembership.objects.filter(user=caller).exists()
