"""Run a bounded, read-only PostgreSQL smoke check."""

from __future__ import annotations

import math
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError, OperationalError, connection, transaction

from green_iteso.accounts.models import User
from green_iteso.actions.models import ActionLog

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0


class SmokeDeadlineExceededError(Exception):
    """Raised when the whole smoke check reaches its deadline."""


@contextmanager
def _deadline(seconds: float) -> Iterator[None]:
    """Put a finite wall-clock bound around libpq connection and queries."""
    # Management commands run in the main process/thread.  SIGALRM covers DNS
    # and address-family fallback too, which libpq's per-host timeout does not
    # necessarily bound.
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        raise RuntimeError(
            "El límite total requiere un entorno Unix; use Docker en Windows."
        )

    previous_handler = signal.getsignal(signal.SIGALRM)

    def raise_deadline(signum: int, frame: object) -> None:
        del signum, frame
        raise SmokeDeadlineExceededError

    signal.signal(signal.SIGALRM, raise_deadline)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _sqlstate(error: BaseException) -> str | None:
    """Return a psycopg SQLSTATE without exposing the exception text."""
    for candidate in (error, error.__cause__):
        if candidate is not None:
            value = getattr(candidate, "sqlstate", None) or getattr(
                candidate, "pgcode", None
            )
            if isinstance(value, str):
                return value
    return None


def _is_missing_schema(error: BaseException) -> bool:
    """Identify PostgreSQL errors caused by an unapplied/incomplete migration."""
    if _sqlstate(error) in {"42P01", "42703"}:  # undefined_table/undefined_column
        return True
    # A few test doubles and older DB adapters omit SQLSTATE.  This bounded
    # pattern is only used for classification; the original text is never
    # included in the command output.
    text = str(error).lower()
    return "does not exist" in text and ("relation" in text or "column" in text)


def _client_ssl_status() -> str:
    """Report libpq's client-side TLS state, never the proxy's server view."""
    raw_connection = connection.connection
    if raw_connection is None:
        return "desconocido"
    # psycopg 3 exposes libpq's client TLS state on PGconn.  ``pg_stat_ssl``
    # describes the server-side leg and is misleading behind a Neon proxy.
    pgconn = getattr(raw_connection, "pgconn", None)
    ssl_in_use = getattr(pgconn, "ssl_in_use", None)
    if ssl_in_use is None:
        # Keep a compatibility fallback for adapters exposing only ``info``;
        # the installed psycopg 3 driver takes the PGconn path above.
        info = getattr(raw_connection, "info", None)
        ssl_in_use = getattr(info, "ssl_in_use", None)
    if ssl_in_use is True:
        return "activo"
    if ssl_in_use is False:
        return "inactivo"
    return "desconocido"


def _set_bounded_options(timeout: float) -> tuple[dict[str, object], dict[str, object]]:
    """Apply only the process-local libpq connection bound."""
    settings_dict = connection.settings_dict
    options = settings_dict.setdefault("OPTIONS", {})
    original = dict(options)
    options["connect_timeout"] = max(1, math.ceil(timeout))
    return options, original


def _set_transaction_bounds(cursor: Any, timeout: float) -> None:
    """Set read transaction bounds without relying on startup options."""
    milliseconds = max(1, math.ceil(timeout * 1000))
    value = f"{milliseconds}ms"
    cursor.execute(
        "SELECT set_config('statement_timeout', %s, true), "
        "set_config('lock_timeout', %s, true)",
        [value, value],
    )


class Command(BaseCommand):
    """Check PostgreSQL reachability and the minimal migrated ORM schema."""

    help = "Ejecuta una comprobación acotada y de solo lectura de PostgreSQL."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--timeout",
            type=float,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="Límite total en segundos (0.1–30; predeterminado: 5).",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        raw_timeout = float(options["timeout"])
        if not 0.1 <= raw_timeout <= MAX_TIMEOUT_SECONDS:
            raise CommandError("--timeout debe estar entre 0.1 y 30 segundos.")

        bounded_options, original_options = _set_bounded_options(raw_timeout)
        phase = "connection"
        try:
            connection.close()
            with _deadline(raw_timeout):
                connection.ensure_connection()
                phase = "read"
                with transaction.atomic():
                    # This makes accidental future writes fail at the database
                    # boundary, in addition to this command containing no DML.
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                        )
                        cursor.execute("SET TRANSACTION READ ONLY")
                        _set_transaction_bounds(cursor, raw_timeout)
                        cursor.execute("SELECT 1")
                        if cursor.fetchone() != (1,):
                            raise DatabaseError(
                                "read probe returned an unexpected value"
                            )
                    user_count = User.objects.count()
                    action_log_count = ActionLog.objects.count()
                    ssl_status = _client_ssl_status()
        except SmokeDeadlineExceededError:
            raise CommandError(
                "DB_SMOKE ERROR\n"
                "diagnostico: CONNECTION_FAILURE\n"
                "detalle: PostgreSQL no respondió dentro del límite configurado."
            ) from None
        except DatabaseError as error:
            if _is_missing_schema(error):
                raise CommandError(
                    "DB_SMOKE ERROR\n"
                    "diagnostico: MISSING_MIGRATIONS\n"
                    "detalle: La conexión funciona, pero falta una tabla o columna de las migraciones. "
                    "Aplique las migraciones revisadas con el rol migrator; este comando no las ejecuta."
                ) from None
            if phase == "connection" or isinstance(error, OperationalError):
                raise CommandError(
                    "DB_SMOKE ERROR\n"
                    "diagnostico: CONNECTION_FAILURE\n"
                    "detalle: No se pudo abrir o mantener la conexión PostgreSQL dentro del límite configurado."
                ) from None
            raise CommandError(
                "DB_SMOKE ERROR\n"
                "diagnostico: READ_FAILURE\n"
                "detalle: La conexión funciona, pero una consulta de solo lectura falló."
            ) from None
        except Exception:  # noqa: BLE001 - redact every unexpected driver error
            # Driver/configuration errors do not always inherit Django's
            # DatabaseError (for example, a failed psycopg connection setup).
            # Never relay their text because it may contain a URL or username.
            diagnosis = (
                "CONNECTION_FAILURE" if phase == "connection" else "READ_FAILURE"
            )
            detail = (
                "No se pudo abrir o mantener la conexión PostgreSQL dentro del límite configurado."
                if phase == "connection"
                else "La conexión funciona, pero una consulta de solo lectura falló."
            )
            raise CommandError(
                f"DB_SMOKE ERROR\ndiagnostico: {diagnosis}\ndetalle: {detail}"
            ) from None
        finally:
            connection.close()
            bounded_options.clear()
            bounded_options.update(original_options)

        self.stdout.write("DB_SMOKE OK")
        self.stdout.write("conexion: OK; SELECT 1: OK")
        self.stdout.write(
            f"orm: User count={user_count}; ActionLog count={action_log_count}"
        )
        self.stdout.write(f"ssl_cliente: {ssl_status}")
