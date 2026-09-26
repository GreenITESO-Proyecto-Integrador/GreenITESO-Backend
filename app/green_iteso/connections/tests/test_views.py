"""Coverage for the /api/v1/friendships/ router."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Friendship, User
from green_iteso.connections.services import send_friend_request


def _user(email: str) -> User:
    return User.objects.create_user(email=email, password="local-only")


@pytest.mark.django_db
def test_list_friendships_requires_authentication() -> None:
    response = APIClient().get("/api/v1/friendships/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_create_friend_request() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    client = APIClient()
    client.force_authenticate(ana)

    response = client.post("/api/v1/friendships/", {"addressee": str(beto.pk)})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["requester"]["email"] == "ana@iteso.mx"
    assert body["addressee"]["email"] == "beto@iteso.mx"


@pytest.mark.django_db
def test_create_friend_request_rejects_self() -> None:
    ana = _user("ana@iteso.mx")
    client = APIClient()
    client.force_authenticate(ana)

    response = client.post("/api/v1/friendships/", {"addressee": str(ana.pk)})

    assert response.status_code == 400


@pytest.mark.django_db
def test_create_friend_request_rejects_duplicate_in_either_direction() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    client = APIClient()
    client.force_authenticate(beto)
    send_friend_request(requester=ana, addressee=beto)

    response = client.post("/api/v1/friendships/", {"addressee": str(ana.pk)})

    assert response.status_code == 400
    assert Friendship.objects.count() == 1


@pytest.mark.django_db
def test_list_friendships_returns_sent_and_received() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    carla = _user("carla@iteso.mx")
    send_friend_request(requester=ana, addressee=beto)
    send_friend_request(requester=carla, addressee=ana)
    client = APIClient()
    client.force_authenticate(ana)

    response = client.get("/api/v1/friendships/")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


@pytest.mark.django_db
def test_list_friendships_filters_by_status() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    carla = _user("carla@iteso.mx")
    accepted = send_friend_request(requester=ana, addressee=beto)
    accepted.status = Friendship.Status.ACCEPTED
    accepted.save(update_fields=["status"])
    send_friend_request(requester=carla, addressee=ana)
    client = APIClient()
    client.force_authenticate(ana)

    response = client.get("/api/v1/friendships/", {"status": "accepted"})

    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "ACCEPTED"


@pytest.mark.django_db
def test_accept_friend_request() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    client = APIClient()
    client.force_authenticate(beto)

    response = client.post(f"/api/v1/friendships/{friendship.pk}/accept/")

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"


@pytest.mark.django_db
def test_reject_friend_request() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    client = APIClient()
    client.force_authenticate(beto)

    response = client.post(f"/api/v1/friendships/{friendship.pk}/reject/")

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


@pytest.mark.django_db
def test_accept_friend_request_rejects_the_original_requester() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    client = APIClient()
    client.force_authenticate(ana)

    response = client.post(f"/api/v1/friendships/{friendship.pk}/accept/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_accept_already_resolved_friend_request_fails() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    client = APIClient()
    client.force_authenticate(beto)
    client.post(f"/api/v1/friendships/{friendship.pk}/accept/")

    response = client.post(f"/api/v1/friendships/{friendship.pk}/reject/")

    assert response.status_code == 400
