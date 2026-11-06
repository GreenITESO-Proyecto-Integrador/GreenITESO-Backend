"""Admins listing, banning and unbanning accounts (/api/v1/users/)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from green_iteso.accounts.models import User


def _user(email: str, **fields: object) -> User:
    return User.objects.create_user(email=email, password="local-only", **fields)


@pytest.fixture
def admin() -> User:
    return _user("admin@iteso.mx", role=User.Role.ADMIN)


@pytest.fixture
def client(admin: User) -> APIClient:
    api = APIClient()
    api.force_authenticate(admin)
    return api


def _banned(email: str) -> User:
    return _user(email, is_active=False)


def _self_deactivated(email: str) -> User:
    return _user(email, is_active=False, deactivated_at=timezone.now())


def _emails(client: APIClient, query: str = "") -> set[str]:
    response = client.get(f"/api/v1/users/{query}")
    assert response.status_code == 200
    return {row["email"] for row in response.json()["results"]}


@pytest.mark.django_db
def test_the_list_tells_banned_from_self_deactivated_accounts(
    client: APIClient,
) -> None:
    _user("ana@iteso.mx", first_name="Ana", nickname="Bici")
    _banned("ban@iteso.mx")
    _self_deactivated("gone@iteso.mx")

    rows = {row["email"]: row for row in client.get("/api/v1/users/").json()["results"]}

    assert rows["ana@iteso.mx"]["status"] == "ACTIVE"
    assert rows["ana@iteso.mx"]["first_name"] == "Ana"
    assert rows["ana@iteso.mx"]["nickname"] == "Bici"
    assert rows["ban@iteso.mx"]["status"] == "BANNED"
    assert rows["gone@iteso.mx"]["status"] == "SELF_DEACTIVATED"


@pytest.mark.django_db
def test_filter_banned_lists_only_accounts_an_admin_switched_off(
    client: APIClient,
) -> None:
    _user("ana@iteso.mx")
    _banned("ban@iteso.mx")
    _self_deactivated("gone@iteso.mx")

    assert _emails(client, "?status=banned") == {"ban@iteso.mx"}


@pytest.mark.django_db
def test_filter_not_banned_keeps_everyone_else(client: APIClient) -> None:
    _user("ana@iteso.mx")
    _banned("ban@iteso.mx")
    _self_deactivated("gone@iteso.mx")

    assert _emails(client, "?status=not_banned") == {
        "admin@iteso.mx",
        "ana@iteso.mx",
        "gone@iteso.mx",
    }


@pytest.mark.django_db
def test_an_unknown_status_filter_is_rejected(client: APIClient) -> None:
    response = client.get("/api/v1/users/?status=everyone")

    assert response.status_code == 400
    assert "status" in response.json()


@pytest.mark.django_db
def test_search_by_user_id_is_an_exact_match(client: APIClient) -> None:
    ana = _user("ana@iteso.mx")
    _user("luis@iteso.mx")

    assert _emails(client, f"?search={ana.pk}") == {"ana@iteso.mx"}


@pytest.mark.django_db
def test_search_by_text_matches_email_names_and_nickname(client: APIClient) -> None:
    _user("ana@iteso.mx", first_name="Ana", last_name="López")
    _user("luis@iteso.mx", nickname="Bici")
    _user("otro@iteso.mx")

    assert _emails(client, "?search=LÓPEZ") == {"ana@iteso.mx"}
    assert _emails(client, "?search=bici") == {"luis@iteso.mx"}
    assert _emails(client, "?search=luis@") == {"luis@iteso.mx"}


@pytest.mark.django_db
def test_search_and_status_filter_work_together(client: APIClient) -> None:
    _user("ana@iteso.mx")
    _banned("ana.ban@iteso.mx")

    assert _emails(client, "?status=banned&search=ana") == {"ana.ban@iteso.mx"}


@pytest.mark.django_db
def test_ban_switches_the_account_off_and_ends_its_sessions(
    client: APIClient, django_capture_on_commit_callbacks: Callable
) -> None:
    target = _user("ana@iteso.mx")
    refresh = RefreshToken.for_user(target)
    access = str(refresh.access_token)

    with patch("green_iteso.accounts.moderation.close_user_sockets") as close_sockets:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"/api/v1/users/{target.pk}/ban/")

    assert response.status_code == 200
    assert response.json()["status"] == "BANNED"
    target.refresh_from_db()
    assert target.is_active is False
    assert target.deactivated_at is None
    assert BlacklistedToken.objects.filter(token__user=target).exists()
    close_sockets.assert_called_once_with(target.pk)
    # The access token it already holds stops working at once.
    holder = APIClient()
    holder.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert holder.get("/api/v1/users/me/").status_code == 401


@pytest.mark.django_db
def test_ban_keeps_the_persons_data(client: APIClient) -> None:
    target = _user("ana@iteso.mx", nickname="Bici")

    client.post(f"/api/v1/users/{target.pk}/ban/")

    target.refresh_from_db()
    assert target.nickname == "Bici"


@pytest.mark.django_db
def test_ban_refuses_the_admins_own_account(client: APIClient, admin: User) -> None:
    response = client.post(f"/api/v1/users/{admin.pk}/ban/")

    assert response.status_code == 400
    assert "your own account" in response.json()["error"]["message"]
    admin.refresh_from_db()
    assert admin.is_active is True


@pytest.mark.django_db
def test_ban_refuses_another_administrator(client: APIClient) -> None:
    other_admin = _user("boss@iteso.mx", role=User.Role.ADMIN)

    response = client.post(f"/api/v1/users/{other_admin.pk}/ban/")

    assert response.status_code == 400
    other_admin.refresh_from_db()
    assert other_admin.is_active is True


@pytest.mark.django_db
def test_ban_refuses_an_account_that_is_already_banned(client: APIClient) -> None:
    target = _banned("ban@iteso.mx")

    response = client.post(f"/api/v1/users/{target.pk}/ban/")

    assert response.status_code == 400
    assert "already banned" in response.json()["error"]["message"]


@pytest.mark.django_db
def test_banning_a_self_deactivated_account_stops_it_coming_back(
    client: APIClient,
) -> None:
    target = _self_deactivated("gone@iteso.mx")

    response = client.post(f"/api/v1/users/{target.pk}/ban/")

    assert response.status_code == 200
    assert response.json()["status"] == "BANNED"
    target.refresh_from_db()
    assert target.deactivated_at is None


@pytest.mark.django_db
def test_unban_lets_the_account_back_in(client: APIClient) -> None:
    target = _banned("ban@iteso.mx")

    response = client.post(f"/api/v1/users/{target.pk}/unban/")

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    target.refresh_from_db()
    assert target.is_active is True


@pytest.mark.django_db
def test_unban_refuses_an_account_that_is_not_banned(client: APIClient) -> None:
    active = _user("ana@iteso.mx")
    deactivated = _self_deactivated("gone@iteso.mx")

    assert client.post(f"/api/v1/users/{active.pk}/unban/").status_code == 400
    # Its owner brings it back by signing in; an admin does not restore it.
    assert client.post(f"/api/v1/users/{deactivated.pk}/unban/").status_code == 400
    deactivated.refresh_from_db()
    assert deactivated.is_active is False


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ban", "unban"])
def test_ban_and_unban_are_for_admins_only(action: str) -> None:
    target = _user("ana@iteso.mx")
    student = _user("student@iteso.mx")
    api = APIClient()

    assert api.post(f"/api/v1/users/{target.pk}/{action}/").status_code == 401
    api.force_authenticate(student)
    assert api.post(f"/api/v1/users/{target.pk}/{action}/").status_code == 403
    target.refresh_from_db()
    assert target.is_active is True


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ban", "unban"])
def test_banning_an_unknown_user_is_a_404(client: APIClient, action: str) -> None:
    response = client.post(
        f"/api/v1/users/00000000-0000-4000-8000-000000000000/{action}/"
    )

    assert response.status_code == 404
