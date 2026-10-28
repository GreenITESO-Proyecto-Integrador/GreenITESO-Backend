"""Clan changes notify the people they affect (T2-45)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.services import ensure_profile
from green_iteso.clans.services import (
    accept_join_request,
    create_clan_invitation,
    create_private_clan,
    dissolve_clan,
    join_clan,
    kick_member,
    leave_clan,
    redeem_clan_invitation,
    reject_join_request,
    transfer_leadership,
)
from green_iteso.notifications.models import Notification

Type = Notification.NotificationType


def _received(user: User, notification_type: str) -> list[Notification]:
    return list(
        Notification.objects.filter(user=user, notification_type=notification_type)
    )


def _add_member(clan: Clan, user: User) -> None:
    ClanMembership.objects.create(
        user=user, clan=clan, status=ClanMembership.Status.ACCEPTED
    )


@pytest.fixture
def named_member(member: User) -> User:
    member.first_name = "Ana"
    member.last_name = "López"
    member.save()
    return member


@pytest.fixture
def invite_clan(leader: User) -> Clan:
    return create_private_clan(
        name="Secreto", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )


@pytest.mark.django_db
def test_a_join_request_tells_the_leader_who_asked(
    leader: User, named_member: User, invite_clan: Clan
) -> None:
    join_clan(clan=invite_clan, user=named_member)

    (notification,) = _received(leader, Type.CLAN_JOIN_REQUEST)
    assert "Ana López" in notification.message
    assert "Secreto" in notification.message
    assert not Notification.objects.filter(user=named_member).exists()


@pytest.mark.django_db
def test_joining_a_public_clan_tells_the_leader(
    leader: User, named_member: User
) -> None:
    clan = create_private_clan(name="Abierto", created_by=leader)

    join_clan(clan=clan, user=named_member)

    (notification,) = _received(leader, Type.CLAN_MEMBER_JOINED)
    assert "Ana López" in notification.message
    assert not _received(leader, Type.CLAN_JOIN_REQUEST)


@pytest.mark.django_db
def test_accepting_and_rejecting_tell_the_applicant(
    leader: User, member: User, successor: User, invite_clan: Clan
) -> None:
    join_clan(clan=invite_clan, user=member)
    join_clan(clan=invite_clan, user=successor)

    accept_join_request(clan=invite_clan, actor=leader, applicant=member)
    reject_join_request(clan=invite_clan, actor=leader, applicant=successor)

    (accepted,) = _received(member, Type.CLAN_REQ_ACCEPTED)
    (rejected,) = _received(successor, Type.CLAN_REQ_REJECTED)
    assert "Secreto" in accepted.message
    assert "Secreto" in rejected.message
    assert not _received(member, Type.CLAN_REQ_REJECTED)


@pytest.mark.django_db
def test_a_refused_decision_creates_no_notification(
    leader: User, member: User, invite_clan: Clan
) -> None:
    with pytest.raises(ValueError, match="No pending join request"):
        accept_join_request(clan=invite_clan, actor=leader, applicant=member)

    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_leaving_tells_the_leader(
    leader: User, named_member: User, invite_clan: Clan
) -> None:
    _add_member(invite_clan, named_member)

    leave_clan(clan=invite_clan, user=named_member)

    (notification,) = _received(leader, Type.CLAN_MEMBER_LEFT)
    assert "Ana López" in notification.message


@pytest.mark.django_db
def test_being_expelled_tells_the_member(
    leader: User, member: User, invite_clan: Clan
) -> None:
    _add_member(invite_clan, member)

    kick_member(clan=invite_clan, actor=leader, target_user=member)

    (notification,) = _received(member, Type.CLAN_KICKED)
    assert "Secreto" in notification.message
    assert not _received(leader, Type.CLAN_KICKED)


@pytest.mark.django_db
def test_a_leadership_transfer_tells_every_member_who_leads_now(
    leader: User, named_member: User, successor: User, invite_clan: Clan
) -> None:
    _add_member(invite_clan, named_member)
    _add_member(invite_clan, successor)
    successor.nickname = "Capi"
    successor.save()

    transfer_leadership(clan=invite_clan, actor=leader, successor=successor)

    for person in (leader, named_member, successor):
        (notification,) = _received(person, Type.CLAN_LEADER_CHANGED)
        assert "Capi" in notification.message


@pytest.mark.django_db
def test_dissolving_tells_the_members_but_not_the_leader_who_did_it(
    leader: User, member: User, successor: User, invite_clan: Clan
) -> None:
    _add_member(invite_clan, member)
    ClanMembership.objects.create(
        user=successor, clan=invite_clan, status=ClanMembership.Status.PENDING
    )

    dissolve_clan(clan=invite_clan, actor=leader)
    dissolve_clan(clan=invite_clan, actor=leader)

    assert len(_received(member, Type.CLAN_DISSOLVED)) == 1
    assert not _received(leader, Type.CLAN_DISSOLVED)
    assert not _received(successor, Type.CLAN_DISSOLVED)


@pytest.mark.django_db
def test_redeeming_an_invite_code_tells_the_leader(
    leader: User, named_member: User, invite_clan: Clan
) -> None:
    _, code = create_clan_invitation(clan=invite_clan, actor=leader)

    redeem_clan_invitation(code=code, user=named_member)

    (notification,) = _received(leader, Type.CLAN_MEMBER_JOINED)
    assert "Ana López" in notification.message


@pytest.mark.django_db
def test_messages_never_include_an_email_address(
    leader: User, member: User, invite_clan: Clan
) -> None:
    join_clan(clan=invite_clan, user=member)
    accept_join_request(clan=invite_clan, actor=leader, applicant=member)
    leave_clan(clan=invite_clan, user=member)

    messages = Notification.objects.values_list("message", flat=True)
    assert messages
    assert not any("@" in message for message in messages)


def _make_private(user: User) -> None:
    profile = ensure_profile(user)
    profile.visibility = UserProfile.Visibility.PRIVATE
    profile.save(update_fields=["visibility"])


@pytest.mark.django_db
def test_a_private_leader_is_named_by_nickname_in_a_leadership_transfer(
    leader: User, member: User, successor: User, invite_clan: Clan
) -> None:
    _add_member(invite_clan, member)
    _add_member(invite_clan, successor)
    successor.first_name = "Sofía"
    successor.last_name = "Reyes"
    successor.nickname = "Capi"
    successor.save()
    _make_private(successor)

    transfer_leadership(clan=invite_clan, actor=leader, successor=successor)

    (notification,) = _received(member, Type.CLAN_LEADER_CHANGED)
    assert "Capi" in notification.message
    assert "Sofía" not in notification.message
    assert "Reyes" not in notification.message


@pytest.mark.django_db
def test_a_private_member_without_nickname_is_named_anonymously(
    leader: User, member: User, invite_clan: Clan
) -> None:
    member.first_name = "Ana"
    member.last_name = "López"
    member.save()
    _make_private(member)
    _add_member(invite_clan, member)

    leave_clan(clan=invite_clan, user=member)

    (notification,) = _received(leader, Type.CLAN_MEMBER_LEFT)
    assert User.ANONYMOUS_DISPLAY_NAME in notification.message
    assert "Ana" not in notification.message


@pytest.mark.django_db
def test_a_join_request_names_the_applicant_like_the_requests_list(
    leader: User, member: User, invite_clan: Clan
) -> None:
    member.first_name = "Ana"
    member.last_name = "López"
    member.save()
    _make_private(member)

    join_clan(clan=invite_clan, user=member)

    (notification,) = _received(leader, Type.CLAN_JOIN_REQUEST)
    assert "Ana López" in notification.message
