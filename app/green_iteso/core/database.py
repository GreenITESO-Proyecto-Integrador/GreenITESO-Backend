"""Read-only PostgreSQL connection checks shared by database commands."""

from django.core.management.base import CommandError
from django.db import DatabaseError, connection


def require_connection_tls(
    *, encrypted: bool, inspection_error: str, mismatch_error: str
) -> None:
    """Require the active database connection to match its TLS policy."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            row = cursor.fetchone()
    except DatabaseError:
        raise CommandError(inspection_error) from None
    if row is None or row[0] is not encrypted:
        raise CommandError(mismatch_error)
