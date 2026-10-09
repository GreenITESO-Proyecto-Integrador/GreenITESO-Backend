"""Read-only PostgreSQL connection checks shared by database commands."""

from django.core.management.base import CommandError
from django.db import DatabaseError, connection


def require_connection_tls(
    *, encrypted: bool, inspection_error: str, mismatch_error: str
) -> None:
    """Require the client-to-proxy PostgreSQL connection to match TLS policy."""
    try:
        connection.ensure_connection()
        ssl_in_use = client_tls_state(connection.connection)
    except DatabaseError:
        raise CommandError(inspection_error) from None
    if ssl_in_use is not encrypted:
        raise CommandError(mismatch_error)


def client_tls_state(raw_connection: object | None) -> bool | None:
    """Return libpq's client-side TLS state, or None when unavailable."""
    if raw_connection is None:
        return None
    pgconn = getattr(raw_connection, "pgconn", None)
    ssl_in_use = getattr(pgconn, "ssl_in_use", None)
    if ssl_in_use is None:
        info = getattr(raw_connection, "info", None)
        ssl_in_use = getattr(info, "ssl_in_use", None)
    return ssl_in_use if isinstance(ssl_in_use, bool) else None
