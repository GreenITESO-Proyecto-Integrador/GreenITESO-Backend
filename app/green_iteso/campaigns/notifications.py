"""Notifications emitted by the campaigns domain.

Callers invoke these helpers inside their own ``transaction.atomic()`` block
so a notification is only persisted when the business change that caused it
is committed. The notifications app is only used through its model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from green_iteso.accounts.models import ClanMembership
from green_iteso.notifications.models import Notification

from .models import Campaign

if TYPE_CHECKING:
    from .models import Mission

BULK_CREATE_BATCH_SIZE = 500
DATE_FORMAT = "%d/%m/%Y"


def _campaign_invite_recipient_ids(campaign: Campaign) -> list[Any]:
    """Return the ids of the users invited to ``campaign``, minus its creator.

    Global campaigns reach every active user. Private campaigns reach the
    members of the target clan, the same users ``CampaignJoinView`` lets join.
    """
    if campaign.scope == Campaign.Scope.GLOBAL:
        recipients = get_user_model().objects.filter(is_active=True)
        id_field = "id"
    else:
        recipients = ClanMembership.objects.filter(clan=campaign.target_clan)
        id_field = "user_id"
    return list(
        recipients.exclude(**{id_field: campaign.creator_id}).values_list(
            id_field, flat=True
        )
    )


def _campaign_invite_content(campaign: Campaign) -> tuple[str, str]:
    """Return the title and message of a campaign invitation."""
    deadline = timezone.localtime(campaign.start_date).strftime(DATE_FORMAT)
    if campaign.scope == Campaign.Scope.GLOBAL:
        title = "Nueva campaña disponible"
        message = f"Se publicó la campaña '{campaign.title}'."
    else:
        title = "Nueva campaña en tu clan"
        message = (
            f"Tu clan '{campaign.target_clan.name}' lanzó la campaña "
            f"'{campaign.title}'."
        )
    message += f" Inscríbete antes del {deadline} para participar."
    return title, message


def notify_campaign_invite(campaign: Campaign) -> int:
    """Invite the campaign's audience to join it; return how many were sent.

    Precondition: the campaign is approved and in PROMOTION, the only status
    in which users can still join.
    """
    title, message = _campaign_invite_content(campaign)
    notifications = [
        Notification(
            user_id=user_id,
            title=title,
            message=message,
            notification_type=Notification.NotificationType.CAMPAIGN_INVITE,
        )
        for user_id in _campaign_invite_recipient_ids(campaign)
    ]
    Notification.objects.bulk_create(notifications, batch_size=BULK_CREATE_BATCH_SIZE)
    return len(notifications)


def notify_mission_completed(user: Any, mission: Mission) -> Notification:
    """Tell ``user`` that they just reached ``mission``'s target."""
    return Notification.objects.create(
        user=user,
        title="¡Misión completada!",
        message=(
            f"Completaste la misión '{mission.action.name}' "
            f"({mission.target_count}/{mission.target_count}) "
            f"de la campaña '{mission.campaign.title}'."
        ),
        notification_type=Notification.NotificationType.MISSION_COMPLETED,
    )
