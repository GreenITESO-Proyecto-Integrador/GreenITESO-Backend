"""Rosters and join requests label people without exposing the email (#189)."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.accounts.services import update_profile
from green_iteso.clans.services import create_private_clan, join_clan


def _user(email: str, **fields: str) -> User:
    user = User.objects.create_user(email=email, password="local-only")
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"nickname": "Bici", "first_name": "Ana", "last_name": "López"}, "Bici"),
        ({"first_name": "Ana", "last_name": "López"}, "Ana López"),
        ({}, "Usuario GreenITESO"),
        ({"nickname": "   ", "first_name": "Ana", "last_name": "López"}, "Ana López"),
    ],
)
@pytest.mark.django_db
def test_public_display_name_prefers_nickname_then_full_name(
    fields: dict[str, str], expected: str
) -> None:
    assert _user("ana@iteso.mx", **fields).public_display_name == expected


@pytest.mark.django_db
def test_join_requests_show_the_applicant_name_not_the_email() -> None:
    leader = _user("lead@iteso.mx", nickname="Leo")
    applicant = _user("ana@iteso.mx", first_name="Ana", last_name="López")
    clan = create_private_clan(
        created_by=leader, name="Secreto", privacy=Clan.Privacy.PRIVATE_INVITE
    )
    join_clan(clan=clan, user=applicant)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.get(f"/api/v1/clans/{clan.pk}/requests/")

    assert response.status_code == 200
    applicant_row = response.json()[0]["user"]
    assert applicant_row["display_name"] == "Ana López"
    assert "ana@iteso.mx" not in response.content.decode()


@pytest.mark.django_db
def test_roster_shows_member_names() -> None:
    leader = _user("lead@iteso.mx", first_name="Leo", last_name="Ríos")
    clan = create_private_clan(created_by=leader, name="Verde")
    client = APIClient()
    client.force_authenticate(leader)

    response = client.get(f"/api/v1/clans/{clan.pk}/")

    assert response.status_code == 200
    assert response.json()["members"][0]["display_name"] == "Leo Ríos"
    assert "lead@iteso.mx" not in response.content.decode()


@pytest.mark.django_db
def test_roster_hides_the_real_name_of_private_profiles(
    django_assert_max_num_queries: Any,
) -> None:
    leader = _user("lead@iteso.mx", first_name="Leo", last_name="Ríos")
    hidden = _user("paula@iteso.mx", first_name="Paula", last_name="Privada")
    nicknamed = _user("nico@iteso.mx", first_name="Nico", last_name="Real")
    nicknamed.nickname = "Nico"
    nicknamed.save(update_fields=["nickname"])
    clan = create_private_clan(created_by=leader, name="Verde")
    for user in (hidden, nicknamed):
        join_clan(clan=clan, user=user)
        update_profile(user=user, visibility=UserProfile.Visibility.PRIVATE)
    outsider = _user("otro@iteso.mx")
    client = APIClient()
    client.force_authenticate(outsider)

    with django_assert_max_num_queries(4):
        response = client.get(f"/api/v1/clans/{clan.pk}/")

    names = {row["user_id"]: row["display_name"] for row in response.json()["members"]}
    assert names[str(hidden.pk)] == "Usuario GreenITESO"
    assert names[str(nicknamed.pk)] == "Nico"
    assert names[str(leader.pk)] == "Leo Ríos"
    assert "Paula" not in response.content.decode()


@pytest.mark.django_db
def test_join_requests_keep_the_full_name_of_private_profiles() -> None:
    leader = _user("lead@iteso.mx")
    applicant = _user("paula@iteso.mx", first_name="Paula", last_name="Privada")
    update_profile(user=applicant, visibility=UserProfile.Visibility.PRIVATE)
    clan = create_private_clan(
        created_by=leader, name="Secreto", privacy=Clan.Privacy.PRIVATE_INVITE
    )
    join_clan(clan=clan, user=applicant)
    client = APIClient()
    client.force_authenticate(leader)

    response = client.get(f"/api/v1/clans/{clan.pk}/requests/")

    assert response.json()[0]["user"]["display_name"] == "Paula Privada"
