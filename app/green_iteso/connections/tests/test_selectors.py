"""Coverage for connections selectors."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import User
from green_iteso.connections.selectors import list_own_friendships
from green_iteso.connections.services import send_friend_request


@pytest.mark.django_db
def test_list_own_friendships_includes_sent_and_received() -> None:
    ana = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    beto = User.objects.create_user(email="beto@iteso.mx", password="local-only")
    carla = User.objects.create_user(email="carla@iteso.mx", password="local-only")
    send_friend_request(requester=ana, addressee=beto)
    send_friend_request(requester=carla, addressee=ana)

    results = list(list_own_friendships(ana))

    assert len(results) == 2


@pytest.mark.django_db
def test_list_own_friendships_excludes_unrelated_pairs() -> None:
    ana = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    beto = User.objects.create_user(email="beto@iteso.mx", password="local-only")
    carla = User.objects.create_user(email="carla@iteso.mx", password="local-only")
    send_friend_request(requester=beto, addressee=carla)

    assert not list_own_friendships(ana)
