"""Create notifications from templates and push them to connected clients."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from itertools import batched
from typing import TYPE_CHECKING

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from rest_framework.renderers import JSONRenderer

from .models import Notification
from .serializers import NotificationSerializer
from .templates import render_notification

if TYPE_CHECKING:
    from green_iteso.accounts.models import User

logger = logging.getLogger(__name__)

NOTIFICATION_EVENT = "notification.created"
BULK_CREATE_BATCH_SIZE = 500


def user_group_name(user_id: object) -> str:
    """Channel-layer group that holds every open socket of one user."""
    return f"notifications_user_{user_id}"


def notify(user: User, notification_type: str, **context: object) -> Notification:
    """Persist a templated notification and deliver it in real time.

    The push is scheduled for after the surrounding transaction commits, so a
    rolled-back audit never announces something that was not saved.
    """
    title, message = render_notification(notification_type, **context)
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
    )
    transaction.on_commit(lambda: push_notification(notification))
    return notification


def notify_many(
    users: Iterable[User], notification_type: str, **context: object
) -> list[Notification]:
    """Persist the same templated notification for every user in one pass.

    The template is rendered once and the rows are inserted in batches of
    ``BULK_CREATE_BATCH_SIZE``. A single ``on_commit`` callback pushes the whole
    batch, so a rollback announces nothing. The inserts are atomic: if a later
    batch fails, no row is kept. ``users`` may be a lazy iterator, which keeps
    large audiences out of memory until they are inserted.
    """
    title, message = render_notification(notification_type, **context)
    notifications: list[Notification] = []
    with transaction.atomic():
        for chunk in batched(users, BULK_CREATE_BATCH_SIZE):
            notifications.extend(
                Notification.objects.bulk_create(
                    [
                        Notification(
                            user=user,
                            title=title,
                            message=message,
                            notification_type=notification_type,
                        )
                        for user in chunk
                    ]
                )
            )
        if notifications:
            transaction.on_commit(lambda: push_notifications(notifications))
    return notifications


def push_notifications(notifications: Iterable[Notification]) -> None:
    """Push each notification to its owner; one failure never stops the rest."""
    for notification in notifications:
        push_notification(notification)


def push_notification(notification: Notification) -> None:
    """Send the notification to the owner's open sockets, never raising."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = json.loads(
        JSONRenderer().render(NotificationSerializer(notification).data)
    )
    try:
        async_to_sync(channel_layer.group_send)(
            user_group_name(notification.user_id),
            {"type": NOTIFICATION_EVENT, "notification": payload},
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # The notification is already stored and listed by the REST API, so a
        # failed live push must not break the action that triggered it.
        logger.exception("Could not push notification %s", notification.pk)
