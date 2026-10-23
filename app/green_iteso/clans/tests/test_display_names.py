"""Rosters and join requests label people without exposing the email (#189)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, User
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
