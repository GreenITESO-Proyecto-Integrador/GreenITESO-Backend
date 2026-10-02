"""WebSocket consumer that streams a user's new notifications."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .models import Notification
from .services import NOTIFICATION_EVENT, user_group_name

AUTH_TIMEOUT_SECONDS = 5
CLOSE_UNAUTHORIZED = 4401


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """Authenticate with a first ``auth`` message, then receive live pushes.

    Browsers cannot set an ``Authorization`` header on a WebSocket, and a token
    in the URL would end up in server logs, so the client sends its access
    token as the first frame instead. The socket is closed when the token
    expires; the client then reconnects with a refreshed one.
    """

    user_id: object | None = None
    group_name: str | None = None
    _timer: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        await self.accept()
        self._schedule_close(AUTH_TIMEOUT_SECONDS)

    async def disconnect(self, code: int) -> None:
        self._cancel_timer()
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        message_type = content.get("type") if isinstance(content, dict) else None
        if self.group_name is None:
            if message_type == "auth":
                await self._authenticate(content.get("token"))
            else:
                await self.close(code=CLOSE_UNAUTHORIZED)
            return
        if message_type == "ping":
            await self.send_json({"type": "pong"})

    async def notification_created(self, event: dict[str, Any]) -> None:
        """Forward a ``notification.created`` group event to the client."""
        await self.send_json(
            {"type": NOTIFICATION_EVENT, "notification": event["notification"]}
        )

    async def _authenticate(self, token: object) -> None:
        user, expires_at = await self._resolve_user(token)
        if user is None:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        self.user_id = user.pk
        self.group_name = user_group_name(user.pk)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        unread_count = await self._unread_count()
        self._schedule_close(max(expires_at - time.time(), 0))
        await self.send_json({"type": "auth.ok", "unread_count": unread_count})

    @database_sync_to_async
    def _resolve_user(self, token: object) -> tuple[Any, float]:
        if not isinstance(token, str) or not token:
            return None, 0
        authentication = JWTAuthentication()
        try:
            validated = authentication.get_validated_token(token.encode())
            user = authentication.get_user(validated)
        except (InvalidToken, TokenError, AuthenticationFailed):
            return None, 0
        return user, float(validated["exp"])

    @database_sync_to_async
    def _unread_count(self) -> int:
        return Notification.objects.filter(user_id=self.user_id, is_read=False).count()

    def _schedule_close(self, delay: float) -> None:
        self._cancel_timer()
        self._timer = asyncio.create_task(self._close_after(delay))

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def _close_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.close(code=CLOSE_UNAUTHORIZED)
