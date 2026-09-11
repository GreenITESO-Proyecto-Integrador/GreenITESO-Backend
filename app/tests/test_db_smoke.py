"""Acceptance checks for the bounded, read-only database smoke command."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from green_iteso.accounts.management.commands.db_smoke import _is_missing_schema


@pytest.mark.django_db(transaction=True)
def test_db_smoke_runs_select_and_minimal_orm_reads_on_postgres() -> None:
    output = StringIO()

    call_command("db_smoke", "--timeout", "2", stdout=output)

    rendered = output.getvalue()
    assert "DB_SMOKE OK" in rendered
    assert "SELECT 1: OK" in rendered
    assert "User count=" in rendered
    assert "ActionLog count=" in rendered
    assert "ssl_cliente:" in rendered


@pytest.mark.django_db(transaction=True)
def test_db_smoke_emits_no_dml() -> None:
    statements: list[str] = []

    def capture(
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: Any,
    ) -> Any:
        del many, context
        statements.append(sql)
        return execute(sql, params)

    with connection.execute_wrapper(capture):
        call_command("db_smoke", "--timeout", "2", stdout=StringIO())

    assert statements
    assert all(
        statement.lstrip().split(maxsplit=1)[0].upper() in {"SELECT", "SET"}
        for statement in statements
    )


def test_missing_schema_sqlstates_are_diagnosed_as_unapplied_migrations() -> None:
    class UndefinedTableError(Exception):
        sqlstate = "42P01"

    class UndefinedColumnError(Exception):
        sqlstate = "42703"

    assert _is_missing_schema(UndefinedTableError())
    assert _is_missing_schema(UndefinedColumnError())
    assert _is_missing_schema(Exception('relation "accounts_user" does not exist'))


def test_connection_failure_does_not_echo_connection_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_url = "postgresql://user:super-secret@example.test:5432/greeniteso"

    def fail() -> None:
        raise RuntimeError(secret_url)

    monkeypatch.setattr(connection, "ensure_connection", fail)

    with pytest.raises(CommandError) as raised:
        call_command(
            "db_smoke",
            "--timeout",
            "1",
            stdout=StringIO(),
            stderr=StringIO(),
            traceback=True,
        )

    message = str(raised.value)
    assert "CONNECTION_FAILURE" in message
    assert secret_url not in message
    assert "super-secret" not in message
    assert raised.value.__cause__ is None
    assert secret_url not in "".join(traceback.format_exception(raised.value))
