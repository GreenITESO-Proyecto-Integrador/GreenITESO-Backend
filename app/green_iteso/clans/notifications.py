"""Notifications emitted by the clans domain (T2-45).

Callers invoke these helpers inside their own ``transaction.atomic()`` block,
so a notification is only persisted, and pushed in real time, when the clan
change that caused it commits. They go through
``green_iteso.notifications.services`` (``notify`` for one person,
``notify_many`` for a whole roster) and never raise because nobody is there to
receive them: a clan without a leader just has nobody to tell.
"""

from __future__ import annotations

from collections.abc import Iterable

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import notify, notify_many

NotificationType = Notification.NotificationType


def _person_name(user: User) -> str:
    """How a member is named in a message: same rule as the clan roster.

    A PRIVATE profile shows only its nickname (else the anonymous label), never
    the real name, and the email is never used.
    """
    return user.name_shown_to_others()


def _leader_of(clan: Clan) -> User | None:
    """Return the clan's current LEADER, or None if it has none."""
    membership = (
        ClanMembership.objects.filter(
            clan=clan,
            role=ClanMembership.MembershipRole.LEADER,
            status=ClanMembership.Status.ACCEPTED,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )
    return membership.user if membership is not None else None


def _members_of(clan: Clan, *, exclude: Iterable[User] = ()) -> list[User]:
    """Return the active users with an ACCEPTED membership, minus ``exclude``."""
    excluded = [user.pk for user in exclude]
    return list(
        User.objects.filter(
            is_active=True,
            clan_memberships__clan=clan,
            clan_memberships__status=ClanMembership.Status.ACCEPTED,
        )
        .exclude(pk__in=excluded)
        .distinct()
    )


def notify_join_request(*, clan: Clan, applicant: User) -> None:
    """Tell the leader that ``applicant`` asked to join an invite-only clan.

    Named like the leader's join-requests list (``public_display_name``), since
    the leader has to recognize who is asking.
    """
    leader = _leader_of(clan)
    if leader is not None and leader.pk != applicant.pk:
        notify(
            leader,
            NotificationType.CLAN_JOIN_REQUEST,
            clan_name=clan.name,
            member_name=applicant.public_display_name,
        )


def notify_member_joined(*, clan: Clan, member: User) -> None:
    """Tell the leader that ``member`` joined (public clan or invite code)."""
    leader = _leader_of(clan)
    if leader is not None and leader.pk != member.pk:
        notify(
            leader,
            NotificationType.CLAN_MEMBER_JOINED,
            clan_name=clan.name,
            member_name=_person_name(member),
        )


def notify_member_left(*, clan: Clan, member: User) -> None:
    """Tell the leader that ``member`` left the clan."""
    leader = _leader_of(clan)
    if leader is not None and leader.pk != member.pk:
        notify(
            leader,
            NotificationType.CLAN_MEMBER_LEFT,
            clan_name=clan.name,
            member_name=_person_name(member),
        )


def notify_request_decided(*, clan: Clan, applicant: User, accepted: bool) -> None:
    """Tell ``applicant`` whether the leader accepted or rejected their request."""
    notify(
        applicant,
        NotificationType.CLAN_REQ_ACCEPTED
        if accepted
        else NotificationType.CLAN_REQ_REJECTED,
        clan_name=clan.name,
    )


def notify_member_kicked(*, clan: Clan, member: User) -> None:
    """Tell ``member`` that the leader expelled them."""
    notify(member, NotificationType.CLAN_KICKED, clan_name=clan.name)


def notify_leader_changed(*, clan: Clan, new_leader: User) -> None:
    """Tell every member, the new leader included, who leads the clan now."""
    notify_many(
        _members_of(clan),
        NotificationType.CLAN_LEADER_CHANGED,
        clan_name=clan.name,
        leader_name=_person_name(new_leader),
    )


def notify_clan_dissolved(*, clan: Clan, actor: User) -> None:
    """Tell every member except the leader who dissolved it."""
    notify_many(
        _members_of(clan, exclude=[actor]),
        NotificationType.CLAN_DISSOLVED,
        clan_name=clan.name,
    )
