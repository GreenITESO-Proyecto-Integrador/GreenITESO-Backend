"""Coverage for join/leave and join-request accept/reject (T2-32, T2-33)."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.services import (
    PrivateClanLimitExceededError,
    accept_join_request,
    create_private_clan,
    join_clan,
    leave_clan,
    reject_join_request,
    transfer_leadership,
)


@pytest.mark.django_db
def test_join_clan_joins_public_clan_directly(clan: Clan) -> None:
    """``clan`` fixture (via create_clan) defaults to Privacy.PUBLIC."""
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    membership = join_clan(clan=clan, user=applicant)

    assert membership.status == ClanMembership.Status.ACCEPTED
    assert ClanMembership.objects.filter(
        user=applicant, clan=clan, status=ClanMembership.Status.ACCEPTED
    ).exists()


@pytest.mark.django_db
def test_join_clan_creates_a_pending_request_for_a_private_invite_clan(
    leader: User,
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    membership = join_clan(clan=invite_clan, user=applicant)

    assert membership.status == ClanMembership.Status.PENDING


@pytest.mark.django_db
def test_join_clan_rejects_a_second_request_while_pending(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(ValueError):
        join_clan(clan=invite_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_rejects_an_already_accepted_member(clan: Clan) -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=clan, user=applicant)

    with pytest.raises(ValueError):
        join_clan(clan=clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_rejects_an_institutional_clan() -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )

    with pytest.raises(ValueError):
        join_clan(clan=institutional_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_enforces_br04_cap_for_a_public_clan() -> None:
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    for i in range(5):
        other_leader = User.objects.create_user(
            email=f"leader{i}@iteso.mx", password="local-only"
        )
        full_clan = create_private_clan(name=f"Clan {i}", created_by=other_leader)
        ClanMembership.objects.create(
            user=applicant, clan=full_clan, role=ClanMembership.MembershipRole.MEMBER
        )
    sixth_leader = User.objects.create_user(
        email="sixth@iteso.mx", password="local-only"
    )
    sixth_clan = create_private_clan(name="Sixth", created_by=sixth_leader)

    with pytest.raises(PrivateClanLimitExceededError):
        join_clan(clan=sixth_clan, user=applicant)


@pytest.mark.django_db
def test_join_clan_lets_a_rejected_applicant_request_again(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)
    reject_join_request(clan=invite_clan, actor=leader, applicant=applicant)

    membership = join_clan(clan=invite_clan, user=applicant)

    assert membership.status == ClanMembership.Status.PENDING


@pytest.mark.django_db
def test_accept_join_request_accepts_a_pending_applicant(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    membership = accept_join_request(
        clan=invite_clan, actor=leader, applicant=applicant
    )

    assert membership.status == ClanMembership.Status.ACCEPTED


@pytest.mark.django_db
def test_accept_join_request_rejects_a_non_leader_actor(
    leader: User, member: User
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=invite_clan)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(PermissionDenied):
        accept_join_request(clan=invite_clan, actor=member, applicant=applicant)


@pytest.mark.django_db
def test_accept_join_request_rejects_a_missing_pending_request(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        accept_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_accept_join_request_enforces_br04_cap_on_the_applicant(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)
    for i in range(5):
        other_leader = User.objects.create_user(
            email=f"leader{i}@iteso.mx", password="local-only"
        )
        full_clan = create_private_clan(name=f"Clan {i}", created_by=other_leader)
        ClanMembership.objects.create(
            user=applicant, clan=full_clan, role=ClanMembership.MembershipRole.MEMBER
        )

    with pytest.raises(PrivateClanLimitExceededError):
        accept_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_reject_join_request_marks_it_rejected(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    membership = reject_join_request(
        clan=invite_clan, actor=leader, applicant=applicant
    )

    assert membership.status == ClanMembership.Status.REJECTED


@pytest.mark.django_db
def test_reject_join_request_rejects_a_non_leader_actor(
    leader: User, member: User
) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=invite_clan)
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    with pytest.raises(PermissionDenied):
        reject_join_request(clan=invite_clan, actor=member, applicant=applicant)


@pytest.mark.django_db
def test_reject_join_request_rejects_a_missing_pending_request(leader: User) -> None:
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        reject_join_request(clan=invite_clan, actor=leader, applicant=applicant)


@pytest.mark.django_db
def test_leave_clan_removes_the_callers_membership(member: User, clan: Clan) -> None:
    ClanMembership.objects.create(user=member, clan=clan)

    leave_clan(clan=clan, user=member)

    assert not ClanMembership.objects.filter(user=member, clan=clan).exists()


@pytest.mark.django_db
def test_leave_clan_rejects_an_institutional_clan(member: User) -> None:
    institutional_clan = Clan.objects.create(
        name="Software Engineering", type=Clan.ClanType.INSTITUTIONAL
    )
    ClanMembership.objects.create(user=member, clan=institutional_clan)

    with pytest.raises(ValueError):
        leave_clan(clan=institutional_clan, user=member)

    assert ClanMembership.objects.filter(user=member, clan=institutional_clan).exists()


@pytest.mark.django_db
def test_leave_clan_rejects_the_leader(leader: User, clan: Clan) -> None:
    with pytest.raises(PermissionDenied):
        leave_clan(clan=clan, user=leader)

    assert ClanMembership.objects.filter(user=leader, clan=clan).exists()


@pytest.mark.django_db
def test_leave_clan_rejects_a_non_member(clan: Clan) -> None:
    outsider = User.objects.create_user(
        email="outsider@iteso.mx", password="local-only"
    )

    with pytest.raises(ValueError):
        leave_clan(clan=clan, user=outsider)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("review", [accept_join_request, reject_join_request])
def test_join_request_review_serializes_against_a_concurrent_leadership_transfer(
    review: Callable[..., ClanMembership], leader: User
) -> None:
    """Regression test for the TOCTOU the review caught in accept/reject.

    The ``transfer_leadership`` below stays uncommitted (and so holds the clan
    row lock) while the former leader tries to review a join request from a
    second thread. The review must block on that lock instead of passing an
    unlocked ``is_leader`` read against the old committed state; once the
    transfer commits, the re-check sees the actor demoted and the review is
    refused, leaving the request PENDING.
    """
    clan = create_private_clan(
        name="Invite Only", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    successor = User.objects.create_user(
        email="successor@iteso.mx", password="local-only"
    )
    ClanMembership.objects.create(
        user=successor, clan=clan, role=ClanMembership.MembershipRole.MEMBER
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=clan, user=applicant)

    outcome: list[Exception | None] = []

    def review_as_former_leader() -> None:
        try:
            review(clan=clan, actor=leader, applicant=applicant)
            outcome.append(None)
        except PermissionDenied as exc:
            outcome.append(exc)
        finally:
            connection.close()

    worker = threading.Thread(target=review_as_former_leader)
    with transaction.atomic():
        transfer_leadership(clan=clan, actor=leader, successor=successor)
        worker.start()
        worker.join(timeout=1.0)
        blocked_on_the_clan_lock = worker.is_alive()
    worker.join()

    assert blocked_on_the_clan_lock
    assert len(outcome) == 1
    assert isinstance(outcome[0], PermissionDenied)
    request = ClanMembership.objects.get(user=applicant, clan=clan)
    assert request.status == ClanMembership.Status.PENDING


@pytest.mark.django_db
def test_api_leader_can_list_pending_requests(leader: User) -> None:
    """List pending join requests for a private clan as its leader (T2-40).

    The API endpoint is GET /clans/{clan_id}/requests.
    """
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    membership = join_clan(clan=invite_clan, user=applicant)

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-requests", kwargs={"pk": invite_clan.id})

    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1
    assert response.data[0]["id"] == str(membership.id)


@pytest.mark.django_db
def test_api_admin_cannot_list_pending_requests_of_a_clan_they_do_not_lead() -> None:
    """Clan management is strictly leader-only: a global ADMIN gets 403 (#164).

    The API endpoint is GET /clans/{clan_id}/requests.
    """
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    leader = User.objects.create_user(
        email="leader_clan@iteso.mx", password="local-only"
    )
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    client = APIClient()
    client.force_authenticate(user=admin)
    url = reverse("clan-requests", kwargs={"pk": invite_clan.id})

    response = client.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_api_non_leader_cannot_list_pending_requests(
    leader: User, member: User
) -> None:
    """A non-leader member of a private clan cannot list its pending join requests.

    The API endpoint is GET /clans/{clan_id}/requests.
    """
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    ClanMembership.objects.create(user=member, clan=invite_clan)

    client = APIClient()
    client.force_authenticate(user=member)
    url = reverse("clan-requests", kwargs={"pk": invite_clan.id})

    response = client.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_api_leader_can_accept_join_request(leader: User) -> None:
    """Accept a pending join request for a private clan as its leader.

    The API endpoint is POST /clans/{clan_id}/accept-request.
    """
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-accept-request", kwargs={"pk": invite_clan.id})

    response = client.post(url, data={"applicant_id": str(applicant.id)})

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == ClanMembership.Status.ACCEPTED


@pytest.mark.django_db
def test_api_leader_can_reject_join_request(leader: User) -> None:
    """Reject a pending join request for a private clan as its leader.

    The API endpoint is POST /clans/{clan_id}/reject-request.
    """
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )
    join_clan(clan=invite_clan, user=applicant)

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-reject-request", kwargs={"pk": invite_clan.id})

    response = client.post(url, data={"applicant_id": str(applicant.id)})

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == ClanMembership.Status.REJECTED


@pytest.mark.django_db
def test_api_field_keyed_error_response_shape_preserved(leader: User) -> None:
    """If the request body is missing a required field, the API returns a 400
    with a field-keyed error response instead of a 500.

    The API endpoint is POST /clans/{clan_id}/accept-request.
    """
    invite_clan = create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    applicant = User.objects.create_user(
        email="applicant@iteso.mx", password="local-only"
    )

    client = APIClient()
    client.force_authenticate(user=leader)
    url = reverse("clan-accept-request", kwargs={"pk": invite_clan.id})

    response = client.post(url, data={"applicant_id": str(applicant.id)})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "applicant_id" in response.data
