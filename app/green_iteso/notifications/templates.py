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
    NotificationType.CLAN_DISSOLVED: NotificationTemplate(
        title="Clan disuelto",
        message='El clan "{clan_name}" fue disuelto por su líder.',
    ),
    NotificationType.CLAN_JOIN_REQUEST: NotificationTemplate(
        title="Nueva solicitud de ingreso",
        message='{member_name} quiere unirse a tu clan "{clan_name}".',
    ),
    NotificationType.CLAN_KICKED: NotificationTemplate(
        title="Saliste de un clan",
        message='El líder te expulsó del clan "{clan_name}".',
    ),
    NotificationType.CLAN_LEADER_CHANGED: NotificationTemplate(
        title="Nuevo líder de clan",
        message='{leader_name} es ahora líder del clan "{clan_name}".',
    ),
    NotificationType.CLAN_MEMBER_JOINED: NotificationTemplate(
        title="Nuevo miembro en tu clan",
        message='{member_name} se unió a tu clan "{clan_name}".',
    ),
    NotificationType.CLAN_MEMBER_LEFT: NotificationTemplate(
        title="Un miembro dejó tu clan",
        message='{member_name} salió de tu clan "{clan_name}".',
    ),
    NotificationType.CLAN_REQ_ACCEPTED: NotificationTemplate(
        title="Solicitud aceptada",
        message='Ya eres parte del clan "{clan_name}".',
    ),
    NotificationType.CLAN_REQ_REJECTED: NotificationTemplate(
        title="Solicitud rechazada",
        message='Tu solicitud para unirte al clan "{clan_name}" fue rechazada.',
    ),
    NotificationType.MISSION_COMPLETED: NotificationTemplate(
        title="Misión completada",
        message='Completaste la misión "{mission_name}" de la campaña "{campaign_name}".',
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
