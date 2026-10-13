"""The in-memory channel layer is only safe while the runtime has one worker."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import override_settings

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
SHARED = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def _ids() -> set[str]:
    return {message.id for message in run_checks()}


def test_makefile_pins_a_single_worker_by_default() -> None:
    recipe = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"--workers \$\$\{WEB_CONCURRENCY:-(\d+)\}", recipe)
    assert match, "gunicorn-asgi must declare its worker count"
    assert match.group(1) == "1", (
        "Do not raise the default worker count while CHANNEL_LAYERS is "
        "InMemoryChannelLayer; configure a shared layer first."
    )


def test_in_memory_layer_is_the_configured_default() -> None:
    # If this fails a shared layer has landed: drop the pin and this guard.
    from django.conf import settings

    assert settings.CHANNEL_LAYERS == IN_MEMORY


@pytest.mark.parametrize("value", [None, "", "1"])
def test_single_worker_passes(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", value)
    with override_settings(CHANNEL_LAYERS=IN_MEMORY):
        assert "notifications.E001" not in _ids()


@pytest.mark.parametrize("value", ["2", "4"])
def test_several_workers_with_in_memory_layer_fail_loudly(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    with override_settings(CHANNEL_LAYERS=IN_MEMORY):
        assert "notifications.E001" in _ids()
        with pytest.raises(SystemCheckError, match="notifications.E001"):
            call_command("check")


def test_invalid_worker_count_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "many")
    with override_settings(CHANNEL_LAYERS=IN_MEMORY):
        assert "notifications.E002" in _ids()


def test_shared_layer_allows_several_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with override_settings(CHANNEL_LAYERS=SHARED):
        assert not {"notifications.E001", "notifications.E002"} & _ids()
