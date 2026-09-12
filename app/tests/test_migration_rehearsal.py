"""Focused failure-path checks for the disposable migration rehearsal."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "rehearse-migration-conflict.py"
)


def _load_rehearsal_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_rehearsal", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load migration rehearsal script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_postgres_removes_container_when_port_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rehearsal = _load_rehearsal_module()
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1] == "port":
            raise subprocess.CalledProcessError(1, command, stderr="port unavailable")
        return subprocess.CompletedProcess(
            command, 0, stdout="container-id\n", stderr=""
        )

    monkeypatch.setattr(rehearsal.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        rehearsal.start_postgres()

    container = calls[0][calls[0].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "--force", container]
