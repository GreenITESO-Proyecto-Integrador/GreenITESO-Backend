"""Coverage for changing a nickname: free once, then paid with points (#175)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User, UserProfile
from green_iteso.accounts.services import (
    NICKNAME_CHANGE_COST_POINTS,
    InsufficientPointsForNicknameError,
    NicknameUnchangedError,
    change_nickname,
    ensure_profile,
)

NICKNAME_URL = "/api/v1/profile/me/nickname/"
PROFILE_URL = "/api/v1/profile/me/"


def _user_with_points(available: int, total: int | None = None) -> User:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    profile = ensure_profile(user)
    profile.available_points = available
    profile.total_points = available if total is None else total
    profile.save(update_fields=["available_points", "total_points"])
    return user


@pytest.mark.django_db
def test_first_nickname_change_is_free() -> None:
    user = _user_with_points(0)

    result = change_nickname(user=user, nickname="EcoAna")

    assert result.nickname == "EcoAna"
    assert result.points_spent == 0
    assert result.available_points == 0
    assert result.next_change_cost == NICKNAME_CHANGE_COST_POINTS
    user.refresh_from_db()
    assert user.nickname == "EcoAna"
    assert ensure_profile(user).nickname_changes == 1


@pytest.mark.django_db
def test_later_changes_cost_points_from_available_but_not_total() -> None:
    user = _user_with_points(available=120, total=500)
    change_nickname(user=user, nickname="First")

    result = change_nickname(user=user, nickname="Second")

    assert result.points_spent == NICKNAME_CHANGE_COST_POINTS
    assert result.available_points == 120 - NICKNAME_CHANGE_COST_POINTS
    profile = UserProfile.objects.get(user=user)
    assert profile.available_points == 120 - NICKNAME_CHANGE_COST_POINTS
    assert profile.total_points == 500
    assert profile.nickname_changes == 2
    user.refresh_from_db()
    assert user.nickname == "Second"


@pytest.mark.django_db
def test_a_paid_change_without_enough_points_changes_nothing() -> None:
    user = _user_with_points(NICKNAME_CHANGE_COST_POINTS - 1)
    change_nickname(user=user, nickname="First")

    with pytest.raises(InsufficientPointsForNicknameError):
        change_nickname(user=user, nickname="Second")

    user.refresh_from_db()
    profile = UserProfile.objects.get(user=user)
    assert user.nickname == "First"
    assert profile.available_points == NICKNAME_CHANGE_COST_POINTS - 1
    assert profile.nickname_changes == 1


@pytest.mark.django_db
def test_exactly_enough_points_is_accepted() -> None:
    user = _user_with_points(NICKNAME_CHANGE_COST_POINTS)
    change_nickname(user=user, nickname="First")

    result = change_nickname(user=user, nickname="Second")

    assert result.available_points == 0


@pytest.mark.django_db
def test_repeating_the_current_nickname_is_rejected_and_not_charged() -> None:
    user = _user_with_points(200)
    change_nickname(user=user, nickname="First")

    with pytest.raises(NicknameUnchangedError):
        change_nickname(user=user, nickname="First")

    profile = UserProfile.objects.get(user=user)
    assert profile.available_points == 200
    assert profile.nickname_changes == 1


@pytest.mark.django_db
def test_a_user_who_already_has_a_nickname_still_gets_one_free_change() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", nickname="Seeded")

    result = change_nickname(user=user, nickname="Fresh")

    assert result.points_spent == 0


@pytest.mark.django_db
def test_endpoint_requires_authentication() -> None:
    response = APIClient().post(NICKNAME_URL, {"nickname": "x"}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_endpoint_changes_the_nickname_and_reports_the_balance() -> None:
    user = _user_with_points(80)
    client = APIClient()
    client.force_authenticate(user)

    free = client.post(NICKNAME_URL, {"nickname": "  EcoAna  "}, format="json")
    paid = client.post(NICKNAME_URL, {"nickname": "EcoAna2"}, format="json")

    assert free.status_code == 200
    assert free.json() == {
        "nickname": "EcoAna",
        "points_spent": 0,
        "available_points": 80,
        "next_change_cost": NICKNAME_CHANGE_COST_POINTS,
    }
    assert paid.status_code == 200
    assert paid.json()["points_spent"] == NICKNAME_CHANGE_COST_POINTS
    assert paid.json()["available_points"] == 80 - NICKNAME_CHANGE_COST_POINTS


@pytest.mark.django_db
def test_endpoint_rejects_not_enough_points_on_the_nickname_field() -> None:
    user = _user_with_points(0)
    client = APIClient()
    client.force_authenticate(user)
    client.post(NICKNAME_URL, {"nickname": "First"}, format="json")

    response = client.post(NICKNAME_URL, {"nickname": "Second"}, format="json")

    assert response.status_code == 400
    assert "costs" in response.json()["nickname"]
    user.refresh_from_db()
    assert user.nickname == "First"


@pytest.mark.django_db
@pytest.mark.parametrize("nickname", ["", "   ", "x" * 51])
def test_endpoint_rejects_blank_or_too_long_nicknames(nickname: str) -> None:
    user = _user_with_points(100)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(NICKNAME_URL, {"nickname": nickname}, format="json")

    assert response.status_code == 400
    assert "nickname" in response.json()
    assert ensure_profile(user).nickname_changes == 0


@pytest.mark.django_db
def test_profile_shows_the_nickname_and_what_the_next_change_costs() -> None:
    user = _user_with_points(100)
    client = APIClient()
    client.force_authenticate(user)

    before = client.get(PROFILE_URL).json()
    client.post(NICKNAME_URL, {"nickname": "EcoAna"}, format="json")
    after = client.get(PROFILE_URL).json()

    assert before["nickname"] == ""
    assert before["nickname_change_cost"] == 0
    assert after["nickname"] == "EcoAna"
    assert after["nickname_change_cost"] == NICKNAME_CHANGE_COST_POINTS
