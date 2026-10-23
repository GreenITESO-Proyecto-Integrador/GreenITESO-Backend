"""Notifications emitted by the campaigns domain.

Callers invoke these helpers inside their own ``transaction.atomic()`` block
so a notification is only persisted, and pushed in real time, when the
business change that caused it is committed. Every notification goes through
``green_iteso.notifications.services.notify``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model

from green_iteso.accounts.models import ClanMembership
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import notify

from .models import Campaign

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from .models import Mission


def _campaign_invite_recipients(campaign: Campaign) -> QuerySet[Any]:
    """Return the users invited to ``campaign``, minus its creator.

    Global campaigns reach every active user. Private campaigns reach the
    members of the target clan, the same users ``CampaignJoinView`` lets join.
    """
    users = get_user_model().objects.exclude(pk=campaign.creator_id)
    if campaign.scope == Campaign.Scope.GLOBAL:
        return users.filter(is_active=True)
    return users.filter(
        clan_memberships__clan=campaign.target_clan,
        clan_memberships__status=ClanMembership.Status.ACCEPTED,
    )


def notify_campaign_invite(campaign: Campaign) -> int:
    """Invite the campaign's audience to join it; return how many were sent.

    Precondition: the campaign is approved and in PROMOTION, the only status
    in which users can still join.

    Sends one ``notify()`` per recipient (one INSERT and one on-commit push
    each), so the cost grows linearly with the audience; a global campaign
    reaches every active user.
    """
    count = 0
    for recipient in _campaign_invite_recipients(campaign).iterator():
        notify(
            recipient,
            Notification.NotificationType.CAMPAIGN_INVITE,
            campaign_name=campaign.title,
        )
        count += 1
    return count


def notify_mission_completed(user: Any, mission: Mission) -> Notification:
    """Tell ``user`` that they just reached ``mission``'s target."""
    return notify(
        user,
        Notification.NotificationType.MISSION_COMPLETED,
        mission_name=mission.action.name,
    )
