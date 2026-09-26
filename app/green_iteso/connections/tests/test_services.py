"""Coverage for connections (friendships) services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Friendship, User
from green_iteso.connections.services import (
    AlreadyFriendsError,
    CannotFriendSelfError,
    FriendRequestAlreadyPendingError,
    FriendRequestNotFoundError,
    FriendRequestNotPendingError,
    respond_to_friend_request,
    send_friend_request,
)


def _user(email: str) -> User:
    return User.objects.create_user(email=email, password="local-only")


@pytest.mark.django_db
def test_send_friend_request_creates_a_pending_row() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")

    friendship = send_friend_request(requester=ana, addressee=beto)

    assert friendship.status == Friendship.Status.PENDING
    assert friendship.requester == ana
    assert friendship.addressee == beto


@pytest.mark.django_db
def test_send_friend_request_rejects_self() -> None:
    ana = _user("ana@iteso.mx")

    with pytest.raises(CannotFriendSelfError):
        send_friend_request(requester=ana, addressee=ana)


@pytest.mark.django_db
def test_send_friend_request_rejects_duplicate_same_direction() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    send_friend_request(requester=ana, addressee=beto)

    with pytest.raises(FriendRequestAlreadyPendingError):
        send_friend_request(requester=ana, addressee=beto)


@pytest.mark.django_db
def test_send_friend_request_rejects_duplicate_reverse_direction() -> None:
    """AC-2: A->B pending blocks a B->A request for the same pair."""
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    send_friend_request(requester=ana, addressee=beto)

    with pytest.raises(FriendRequestAlreadyPendingError):
        send_friend_request(requester=beto, addressee=ana)

    assert Friendship.objects.count() == 1


@pytest.mark.django_db
def test_send_friend_request_rejects_when_already_friends() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    respond_to_friend_request(friendship_id=friendship.pk, responder=beto, accept=True)

    with pytest.raises(AlreadyFriendsError):
        send_friend_request(requester=beto, addressee=ana)


@pytest.mark.django_db
def test_send_friend_request_after_rejection_reuses_the_row() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    first = send_friend_request(requester=ana, addressee=beto)
    respond_to_friend_request(friendship_id=first.pk, responder=beto, accept=False)

    second = send_friend_request(requester=beto, addressee=ana)

    assert second.pk == first.pk
    assert second.status == Friendship.Status.PENDING
    assert second.requester == beto
    assert second.addressee == ana
    assert second.responded_at is None
    assert Friendship.objects.count() == 1


@pytest.mark.django_db
def test_respond_to_friend_request_accept() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)

    accepted = respond_to_friend_request(
        friendship_id=friendship.pk, responder=beto, accept=True
    )

    assert accepted.status == Friendship.Status.ACCEPTED
    assert accepted.responded_at is not None


@pytest.mark.django_db
def test_respond_to_friend_request_reject() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)

    rejected = respond_to_friend_request(
        friendship_id=friendship.pk, responder=beto, accept=False
    )

    assert rejected.status == Friendship.Status.REJECTED
    assert rejected.responded_at is not None


@pytest.mark.django_db
def test_respond_to_friend_request_rejects_the_requester_responding() -> None:
    """Only the addressee can accept/reject; the sender cannot self-approve."""
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)

    with pytest.raises(FriendRequestNotFoundError):
        respond_to_friend_request(
            friendship_id=friendship.pk, responder=ana, accept=True
        )


@pytest.mark.django_db
def test_respond_to_friend_request_rejects_unrelated_user() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    carla = _user("carla@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)

    with pytest.raises(FriendRequestNotFoundError):
        respond_to_friend_request(
            friendship_id=friendship.pk, responder=carla, accept=True
        )


@pytest.mark.django_db
def test_respond_to_friend_request_rejects_missing_friendship() -> None:
    beto = _user("beto@iteso.mx")

    with pytest.raises(FriendRequestNotFoundError):
        respond_to_friend_request(
            friendship_id="00000000-0000-0000-0000-000000000000",
            responder=beto,
            accept=True,
        )


@pytest.mark.django_db
def test_respond_to_friend_request_rejects_already_resolved() -> None:
    ana = _user("ana@iteso.mx")
    beto = _user("beto@iteso.mx")
    friendship = send_friend_request(requester=ana, addressee=beto)
    respond_to_friend_request(friendship_id=friendship.pk, responder=beto, accept=True)

    with pytest.raises(FriendRequestNotPendingError):
        respond_to_friend_request(
            friendship_id=friendship.pk, responder=beto, accept=False
        )
