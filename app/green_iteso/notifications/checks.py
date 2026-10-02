"""Fail closed when real-time delivery cannot work across worker processes."""

from __future__ import annotations

import os

from django.conf import settings
from django.core.checks import CheckMessage, Error

IN_MEMORY_LAYER = "channels.layers.InMemoryChannelLayer"


def web_concurrency() -> int:
    """Worker count Gunicorn will use; one when WEB_CONCURRENCY is unset."""
    raw = os.environ.get("WEB_CONCURRENCY", "").strip()
    return int(raw) if raw else 1


def check_channel_layer_matches_workers(**_kwargs: object) -> list[CheckMessage]:
    """Reject several workers while the channel layer is per-process.

    ``InMemoryChannelLayer`` only reaches sockets held by the same process, so
    with more than one worker a saved notification silently skips most
    connected users. ``make gunicorn-asgi`` runs ``check --deploy`` before it
    starts serving, which turns this into a startup failure.
    """
    backend = settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND")
    if backend != IN_MEMORY_LAYER:
        return []
    try:
        workers = web_concurrency()
    except ValueError:
        return [
            Error(
                "WEB_CONCURRENCY must be an integer.",
                id="notifications.E002",
            )
        ]
    if workers <= 1:
        return []
    return [
        Error(
            f"WEB_CONCURRENCY={workers} with InMemoryChannelLayer: real-time "
            "notifications only reach sockets in the same worker process.",
            hint=(
                "Set WEB_CONCURRENCY=1 (and one instance) until a shared "
                "channel layer such as channels-redis is configured. "
                "See docs/deployment.md."
            ),
            id="notifications.E001",
        )
    ]
