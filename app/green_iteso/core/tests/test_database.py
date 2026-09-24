"""Tests for safe client-side database connection checks."""

from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from green_iteso.core import database


@pytest.mark.parametrize("ssl_in_use", [True, False])
def test_client_tls_state_uses_libpq_pgconn(ssl_in_use: bool) -> None:
    raw_connection = SimpleNamespace(pgconn=SimpleNamespace(ssl_in_use=ssl_in_use))

    assert database.client_tls_state(raw_connection) is ssl_in_use


def test_client_tls_state_uses_connection_info_fallback() -> None:
    raw_connection = SimpleNamespace(info=SimpleNamespace(ssl_in_use=True))

    assert database.client_tls_state(raw_connection) is True


def test_client_tls_state_is_unknown_without_raw_connection() -> None:
    assert database.client_tls_state(None) is None


@pytest.mark.parametrize(
    ("ssl_in_use", "encrypted", "should_pass"),
    [
        (True, True, True),
        (False, False, True),
        (True, False, False),
        (False, True, False),
        (None, True, False),
    ],
)
def test_require_connection_tls_fails_closed_on_mismatch_or_unknown(
    monkeypatch: pytest.MonkeyPatch,
    ssl_in_use: bool | None,
    encrypted: bool,
    should_pass: bool,
) -> None:
    monkeypatch.setattr(database.connection, "ensure_connection", lambda: None)
    monkeypatch.setattr(database, "client_tls_state", lambda _raw: ssl_in_use)

    if should_pass:
        database.require_connection_tls(
            encrypted=encrypted,
            inspection_error="inspection failed",
            mismatch_error="TLS required",
        )
    else:
        with pytest.raises(CommandError, match="TLS required"):
            database.require_connection_tls(
                encrypted=encrypted,
                inspection_error="inspection failed",
                mismatch_error="TLS required",
            )
