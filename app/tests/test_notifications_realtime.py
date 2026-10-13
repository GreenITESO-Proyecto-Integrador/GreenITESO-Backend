"""Templates, the notify service and the real-time WebSocket channel."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.notifications.consumers import CLOSE_UNAUTHORIZED
from green_iteso.notifications.models import Notification
from green_iteso.notifications.routing import websocket_urlpatterns
from green_iteso.notifications.services import notify
from green_iteso.notifications.templates import (
    NOTIFICATION_TEMPLATES,
    render_notification,
)

NotificationType = Notification.NotificationType
APPLICATION = URLRouter(websocket_urlpatterns)

# Context that satisfies the placeholders of every template.
FULL_CONTEXT: dict[str, Any] = {
    "action_name": "Reciclaje",
    "points": 10,
    "reason": "Foto borrosa",
    "badge_name": "Guardián del agua",
    "campaign_name": "Semana sin plásticos",
    "mission_name": "Rechaza un popote",
    "follower_name": "carlos.iteso",
    "title": "Mantenimiento",
    "message": "El sistema estará en mantenimiento.",
}


def _token_for(user: User, lifetime: timedelta | None = None) -> str:
    token = AccessToken.for_user(user)
    if lifetime is not None:
        token.set_exp(from_time=token.current_time, lifetime=lifetime)
    return str(token)


async def _connect() -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(APPLICATION, "/ws/notifications/")
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _authenticated(user: User) -> WebsocketCommunicator:
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": _token_for(user)})
    assert (await communicator.receive_json_from())["type"] == "auth.ok"
    return communicator


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="owner@iteso.mx", password="local-password")


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user(email="other@iteso.mx", password="local-password")


# --- templates ---------------------------------------------------------------


def test_every_notification_type_has_a_template() -> None:
    assert set(NOTIFICATION_TEMPLATES) == set(NotificationType.values)


@pytest.mark.parametrize("notification_type", NotificationType.values)
def test_template_renders_with_full_context(notification_type: str) -> None:
    title, message = render_notification(notification_type, **FULL_CONTEXT)
    assert title
    assert message
    assert len(title) <= 150
    assert "{" not in title + message


def test_render_fills_placeholders() -> None:
    title, message = render_notification(
        NotificationType.BADGE_EARNED, badge_name="Guardián del agua"
    )
    assert title == "¡Nueva insignia desbloqueada!"
    assert message == 'Obtuviste la insignia "Guardián del agua".'


def test_render_does_not_interpret_braces_in_values() -> None:
    _, message = render_notification(NotificationType.AUDIT_REJECT, reason="{secret}")
    assert message == "Tu evidencia fue rechazada. Motivo: {secret}"


def test_render_reports_missing_context_value() -> None:
    with pytest.raises(ValueError, match="badge_name"):
        render_notification(NotificationType.BADGE_EARNED)


def test_render_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="No template"):
        render_notification("NOPE")


# --- service -----------------------------------------------------------------


@pytest.mark.django_db
def test_notify_stores_rendered_notification(
    user: User, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        notification = notify(
            user, NotificationType.SYSTEM, title="Hola", message="Mundo"
        )
    notification.refresh_from_db()
    assert notification.user == user
    assert (notification.title, notification.message) == ("Hola", "Mundo")
    assert notification.notification_type == NotificationType.SYSTEM
    assert notification.is_read is False


@pytest.mark.django_db
def test_notify_with_missing_context_creates_nothing(user: User) -> None:
    with pytest.raises(ValueError):
        notify(user, NotificationType.BADGE_EARNED)
    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_push_is_deferred_until_commit(
    user: User, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        notify(user, NotificationType.SYSTEM, title="Hola", message="Mundo")
    assert len(callbacks) == 1


@pytest.mark.django_db
def test_failed_push_does_not_break_the_caller(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    class BrokenLayer:
        async def group_send(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("layer down")

    monkeypatch.setattr(
        "green_iteso.notifications.services.get_channel_layer", lambda: BrokenLayer()
    )
    with django_capture_on_commit_callbacks(execute=True):
        notification = notify(
            user, NotificationType.SYSTEM, title="Hola", message="Mundo"
        )
    assert Notification.objects.filter(pk=notification.pk).exists()


# --- WebSocket ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_authenticated_socket_receives_live_notification(user: User) -> None:
    communicator = await _authenticated(user)
    await database_sync_to_async(notify)(
        user, NotificationType.BADGE_EARNED, badge_name="Guardián del agua"
    )
    event = await communicator.receive_json_from()
    assert event["type"] == "notification.created"
    notification = event["notification"]
    assert notification["notification_type"] == "BADGE_EARNED"
    assert notification["title"] == "¡Nueva insignia desbloqueada!"
    assert notification["is_read"] is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_auth_ok_reports_unread_count(user: User) -> None:
    await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="a", message="b"
    )
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": _token_for(user)})
    assert await communicator.receive_json_from() == {
        "type": "auth.ok",
        "unread_count": 1,
    }
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_auth_ok_unread_count_ignores_soft_deleted(user: User) -> None:
    kept = await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="a", message="b"
    )
    gone = await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="c", message="d"
    )
    await database_sync_to_async(Notification.objects.filter(pk=gone.pk).update)(
        deleted_at=timezone.now()
    )
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": _token_for(user)})
    assert (await communicator.receive_json_from())["unread_count"] == 1
    assert kept.pk != gone.pk
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_notifications_are_not_delivered_to_other_users(
    user: User, other_user: User
) -> None:
    owner_socket = await _authenticated(user)
    other_socket = await _authenticated(other_user)
    await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="a", message="b"
    )
    assert (await owner_socket.receive_json_from())["type"] == "notification.created"
    assert await other_socket.receive_nothing(timeout=0.2)
    await owner_socket.disconnect()
    await other_socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_open_socket_of_a_user_gets_the_notification(user: User) -> None:
    first, second = await _authenticated(user), await _authenticated(user)
    await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="a", message="b"
    )
    assert (await first.receive_json_from())["type"] == "notification.created"
    assert (await second.receive_json_from())["type"] == "notification.created"
    await first.disconnect()
    await second.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disconnected_socket_stops_receiving(user: User) -> None:
    communicator = await _authenticated(user)
    await communicator.disconnect()
    await database_sync_to_async(notify)(
        user, NotificationType.SYSTEM, title="a", message="b"
    )
    assert await communicator.receive_nothing(timeout=0.2)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ping_gets_pong(user: User) -> None:
    communicator = await _authenticated(user)
    await communicator.send_json_to({"type": "ping"})
    assert await communicator.receive_json_from() == {"type": "pong"}
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["not-a-jwt", "", None, 123])
async def test_invalid_token_is_rejected(token: object) -> None:
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": token})
    output = await communicator.receive_output()
    assert output == {"type": "websocket.close", "code": CLOSE_UNAUTHORIZED}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refresh_token_is_rejected_as_access_token(user: User) -> None:
    refresh = await database_sync_to_async(RefreshToken.for_user)(user)
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": str(refresh)})
    assert (await communicator.receive_output())["code"] == CLOSE_UNAUTHORIZED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_inactive_user_is_rejected(user: User) -> None:
    user.is_active = False
    await database_sync_to_async(user.save)()
    communicator = await _connect()
    await communicator.send_json_to({"type": "auth", "token": _token_for(user)})
    assert (await communicator.receive_output())["code"] == CLOSE_UNAUTHORIZED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_first_message_must_be_auth() -> None:
    communicator = await _connect()
    await communicator.send_json_to({"type": "ping"})
    assert (await communicator.receive_output())["code"] == CLOSE_UNAUTHORIZED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_socket_closes_when_auth_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "green_iteso.notifications.consumers.AUTH_TIMEOUT_SECONDS", 0.05
    )
    communicator = await _connect()
    assert (await communicator.receive_output(timeout=1))["code"] == CLOSE_UNAUTHORIZED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_socket_closes_when_token_expires(user: User) -> None:
    communicator = await _connect()
    await communicator.send_json_to(
        {"type": "auth", "token": _token_for(user, lifetime=timedelta(seconds=1))}
    )
    assert (await communicator.receive_json_from())["type"] == "auth.ok"
    assert (await communicator.receive_output(timeout=3))["code"] == CLOSE_UNAUTHORIZED


# --- audit flow --------------------------------------------------------------


def _pending_log(user: User) -> ActionLog:
    UserProfile.objects.get_or_create(user=user)
    clan = Clan.objects.create(name="Institutional", type=Clan.ClanType.INSTITUTIONAL)
    category = ActionCategory.objects.create(code="water", name="Water")
    action = ActionMaster.objects.create(
        code="refill",
        category=category,
        name="Recarga de botella",
        description="Refill a bottle",
        points=5,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="key-1",
        points_awarded=5,
        status=ActionLog.Status.PENDING_AUDIT,
        evidence_object_key="evidence/1.jpg",
    )


@pytest.fixture
def admin_client() -> APIClient:
    admin = User.objects.create_user(email="admin@iteso.mx", password="x", role="ADMIN")
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_approving_an_audit_notifies_the_user_live(
    user: User, admin_client: APIClient
) -> None:
    log = await database_sync_to_async(_pending_log)(user)
    communicator = await _authenticated(user)
    response = await database_sync_to_async(admin_client.patch)(
        f"/api/v1/action-logs/{log.id}/audit/", {"status": "APPROVED"}, format="json"
    )
    assert response.status_code == 200
    event = await communicator.receive_json_from()
    assert event["notification"]["notification_type"] == "AUDIT_APPROVED"
    assert event["notification"]["message"] == (
        "Tu registro de Recarga de botella fue validado. Ganaste 5 puntos."
    )
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_rejecting_an_audit_notifies_the_user_live(
    user: User, admin_client: APIClient
) -> None:
    log = await database_sync_to_async(_pending_log)(user)
    communicator = await _authenticated(user)
    response = await database_sync_to_async(admin_client.patch)(
        f"/api/v1/action-logs/{log.id}/audit/",
        {"status": "REJECTED", "rejection_reason": "Foto borrosa"},
        format="json",
    )
    assert response.status_code == 200
    event = await communicator.receive_json_from()
    assert event["notification"]["notification_type"] == "AUDIT_REJECT"
    assert (
        event["notification"]["message"]
        == "Tu evidencia fue rechazada. Motivo: Foto borrosa"
    )
    await communicator.disconnect()
