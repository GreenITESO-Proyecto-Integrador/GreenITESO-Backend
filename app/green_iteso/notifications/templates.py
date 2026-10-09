"""Message templates for every notification type.

Callers never write notification copy by hand: they pass a type plus the
context values and ``render_notification`` fills the template.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Notification

NotificationType = Notification.NotificationType


@dataclass(frozen=True)
class NotificationTemplate:
    """Title and message patterns using ``str.format`` placeholders."""

    title: str
    message: str

    def render(self, **context: object) -> tuple[str, str]:
        """Fill the placeholders, naming the missing key if one is absent."""
        try:
            return self.title.format(**context), self.message.format(**context)
        except KeyError as error:
            raise ValueError(
                f"Missing template context value: {error.args[0]}"
            ) from error


NOTIFICATION_TEMPLATES: dict[str, NotificationTemplate] = {
    NotificationType.AUDIT_APPROVED: NotificationTemplate(
        title="Evidencia aprobada",
        message="Tu registro de {action_name} fue validado. Ganaste {points} puntos.",
    ),
    NotificationType.AUDIT_REJECT: NotificationTemplate(
        title="Evidencia Rechazada",
        message="Tu evidencia fue rechazada. Motivo: {reason}",
    ),
    NotificationType.BADGE_EARNED: NotificationTemplate(
        title="¡Nueva insignia desbloqueada!",
        message='Obtuviste la insignia "{badge_name}".',
    ),
    NotificationType.CAMPAIGN_INVITE: NotificationTemplate(
        title="Te invitaron a una campaña",
        message='Fuiste invitado a la campaña "{campaign_name}".',
    ),
    NotificationType.MISSION_COMPLETED: NotificationTemplate(
        title="Misión completada",
        message='Completaste la misión "{mission_name}".',
    ),
    NotificationType.SOCIAL_FOLLOW: NotificationTemplate(
        title="Nuevo seguidor",
        message="{follower_name} ahora sigue tu actividad.",
    ),
    NotificationType.SYSTEM: NotificationTemplate(
        title="{title}",
        message="{message}",
    ),
}


def render_notification(notification_type: str, **context: object) -> tuple[str, str]:
    """Return ``(title, message)`` for ``notification_type`` and ``context``."""
    try:
        template = NOTIFICATION_TEMPLATES[notification_type]
    except KeyError as error:
        raise ValueError(
            f"No template for notification type {notification_type!r}"
        ) from error
    return template.render(**context)
