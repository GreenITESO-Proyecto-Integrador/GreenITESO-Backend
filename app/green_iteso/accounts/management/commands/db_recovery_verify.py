"""Create and compare a read-only recovery integrity baseline."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError, OperationalError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce

from green_iteso.accounts.management.commands.db_smoke import (
    SmokeDeadlineExceededError,
    _deadline,
    _is_missing_schema,
    _restore_bounded_options,
    _set_bounded_options,
    _set_transaction_bounds,
)
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import (
    ActionCategory,
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)

BASELINE_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0

TABLE_MODELS = (
    User,
    Clan,
    ClanMembership,
    UserProfile,
    ActionCategory,
    ActionMaster,
    ActionLog,
    ActionLogMissionContribution,
    Campaign,
    Mission,
    CampaignParticipant,
    UserMissionProgress,
)

HISTORY_FIELDS = (
    "id",
    "user_id",
    "action_id",
    "institutional_clan_id",
    "credited_private_clan_id",
    "campaign_id",
    "status",
    "points_awarded",
)


def _marker_id(value: str, label: str) -> str:
    """Validate a synthetic marker identifier without logging it."""
    if not value or len(value) > 128 or any(character.isspace() for character in value):
        raise CommandError(f"{label} debe ser un identificador sintético válido.")
    try:
        return str(UUID(value))
    except ValueError:
        raise CommandError(f"{label} debe ser un UUID sintético válido.") from None


def _migration_set() -> list[str]:
    """Return applied migrations in a stable, non-sensitive representation."""
    applied = MigrationRecorder(connection).applied_migrations()
    return sorted(f"{app_label}.{name}" for app_label, name in applied)


def _pending_code_migrations(applied: list[str]) -> list[str]:
    """Return migration graph leaves absent from the database."""
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    leaves = {f"{app_label}.{name}" for app_label, name in loader.graph.leaf_nodes()}
    return sorted(leaves - set(applied))


def _table_counts() -> dict[str, int]:
    """Count the core tables without selecting row data."""
    return {model._meta.db_table: model.objects.count() for model in TABLE_MODELS}


def _status_totals() -> dict[str, dict[str, int]]:
    """Summarize action history by status and awarded points."""
    rows = (
        ActionLog.objects.values("status")
        .annotate(count=Count("pk"), points=Coalesce(Sum("points_awarded"), Value(0)))
        .order_by("status")
    )
    return {
        str(row["status"]): {
            "count": int(row["count"]),
            "points": int(row["points"] or 0),
        }
        for row in rows
    }


def _attribution_totals() -> list[dict[str, Any]]:
    """Summarize frozen clan attribution using IDs and aggregate values only."""
    rows = (
        ActionLog.objects.values("institutional_clan_id", "credited_private_clan_id")
        .annotate(count=Count("pk"), points=Coalesce(Sum("points_awarded"), Value(0)))
        .order_by("institutional_clan_id", "credited_private_clan_id")
    )
    return [
        {
            "institutional_clan_id": str(row["institutional_clan_id"]),
            "credited_private_clan_id": (
                str(row["credited_private_clan_id"])
                if row["credited_private_clan_id"] is not None
                else None
            ),
            "count": int(row["count"]),
            "points": int(row["points"] or 0),
        }
        for row in rows
    ]


def _history_fingerprint() -> dict[str, Any]:
    """Hash each ActionLog's stable history fields, without retaining PII."""
    row_hashes: list[str] = []
    rows = ActionLog.objects.values(*HISTORY_FIELDS).order_by("id").iterator()
    for row in rows:
        normalized = {
            field: str(row[field]) if row[field] is not None else None
            for field in HISTORY_FIELDS
        }
        encoded = json.dumps(
            normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        row_hashes.append(sha256(encoded).hexdigest())
    ordered = "\n".join(row_hashes).encode("ascii")
    return {
        "algorithm": "sha256",
        "count": len(row_hashes),
        "digest": sha256(ordered).hexdigest(),
    }


def _fk_orphans() -> dict[str, int]:
    """Check ActionLog foreign-key targets without selecting any row payload."""
    checks = {
        "user": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN accounts_user AS target ON target.id = log.user_id
            WHERE target.id IS NULL
        """,
        "action": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN actions_actionmaster AS target ON target.id = log.action_id
            WHERE target.id IS NULL
        """,
        "institutional_clan": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN accounts_clan AS target ON target.id = log.institutional_clan_id
            WHERE target.id IS NULL
        """,
        "credited_private_clan": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN accounts_clan AS target ON target.id = log.credited_private_clan_id
            WHERE log.credited_private_clan_id IS NOT NULL AND target.id IS NULL
        """,
        "campaign": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN campaigns_campaign AS target ON target.id = log.campaign_id
            WHERE log.campaign_id IS NOT NULL AND target.id IS NULL
        """,
        "reviewed_by": """
            SELECT COUNT(*) FROM actions_actionlog AS log
            LEFT JOIN accounts_user AS target ON target.id = log.reviewed_by_id
            WHERE log.reviewed_by_id IS NOT NULL AND target.id IS NULL
        """,
    }
    result: dict[str, int] = {}
    with connection.cursor() as cursor:
        for name, query in checks.items():
            cursor.execute(query)
            result[name] = int(cursor.fetchone()[0])
    return result


def _marker_presence(pre_id: str, post_id: str) -> dict[str, Any]:
    """Record only whether the two synthetic marker IDs exist."""
    return {
        "pre_id": pre_id,
        "post_id": post_id,
        "pre_present": ActionLog.objects.filter(pk=pre_id).exists(),
        "post_present": ActionLog.objects.filter(pk=post_id).exists(),
    }


def _snapshot(pre_id: str, post_id: str) -> dict[str, Any]:
    """Build the complete metadata-only recovery snapshot."""
    applied = _migration_set()
    return {
        "format": BASELINE_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "migrations": applied,
        "pending_code_migrations": _pending_code_migrations(applied),
        "tables": _table_counts(),
        "action_logs": {
            "by_status": _status_totals(),
            "attribution": _attribution_totals(),
            "history_fingerprint": _history_fingerprint(),
            "fk_orphans": _fk_orphans(),
        },
        "markers": _marker_presence(pre_id, post_id),
    }


def _load_baseline(path: Path) -> dict[str, Any]:
    """Load and minimally validate a baseline without echoing file contents."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CommandError("No se pudo leer el baseline de recuperación.") from None
    if not isinstance(value, dict) or value.get("format") != BASELINE_VERSION:
        raise CommandError(
            "El baseline de recuperación no tiene un formato compatible."
        )
    _validate_snapshot(value, "El baseline")
    return value


def _validate_snapshot(snapshot: dict[str, Any], label: str) -> None:
    """Reject malformed or internally unsafe evidence before comparison."""
    migrations = snapshot.get("migrations")
    pending = snapshot.get("pending_code_migrations")
    if not isinstance(migrations, list) or not all(
        isinstance(item, str) and item for item in migrations
    ):
        raise CommandError(f"{label} no contiene un conjunto de migraciones válido.")
    if not isinstance(pending, list) or not all(
        isinstance(item, str) and item for item in pending
    ):
        raise CommandError(f"{label} no contiene el estado de migraciones válido.")
    if pending:
        raise CommandError(
            f"{label} indica migraciones de código pendientes; no es evidencia apta."
        )

    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or any(
        not _is_nonnegative_integer(value) for value in tables.values()
    ):
        raise CommandError(f"{label} no contiene conteos de tablas válidos.")

    markers = snapshot.get("markers")
    if not isinstance(markers, dict):
        raise CommandError(f"{label} debe incluir los dos marcadores sintéticos.")
    pre_id = markers.get("pre_id")
    post_id = markers.get("post_id")
    if not isinstance(pre_id, str) or not isinstance(post_id, str):
        raise CommandError(f"{label} debe incluir IDs de marcador válidos.")
    pre_id = _marker_id(pre_id, "pre_id")
    post_id = _marker_id(post_id, "post_id")
    markers["pre_id"] = pre_id
    markers["post_id"] = post_id
    if pre_id == post_id:
        raise CommandError(f"{label} debe incluir marcadores sintéticos distintos.")

    action_logs = snapshot.get("action_logs")
    if not isinstance(action_logs, dict):
        raise CommandError(f"{label} no contiene el resumen de ActionLog requerido.")
    if not isinstance(action_logs.get("by_status"), dict) or not isinstance(
        action_logs.get("attribution"), list
    ):
        raise CommandError(
            f"{label} no contiene los agregados de ActionLog requeridos."
        )
    fk_orphans = action_logs.get("fk_orphans")
    if not isinstance(fk_orphans, dict) or any(
        not _is_zero_integer(value) for value in fk_orphans.values()
    ):
        raise CommandError(
            f"{label} contiene referencias foráneas huérfanas; no es evidencia íntegra."
        )
    history = action_logs.get("history_fingerprint")
    if not _is_valid_history_fingerprint(history):
        raise CommandError(f"{label} no contiene la huella histórica requerida.")


def _is_nonnegative_integer(value: object) -> bool:
    """Accept JSON counts while excluding booleans from the integer type."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_zero_integer(value: object) -> bool:
    """Accept only a real integer zero for an orphan check."""
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _is_valid_history_fingerprint(value: object) -> bool:
    """Validate the public shape of the metadata-only history digest."""
    if not isinstance(value, dict):
        return False
    digest = value.get("digest")
    return (
        value.get("algorithm") == "sha256"
        and _is_nonnegative_integer(value.get("count"))
        and isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return stable mismatch codes, never row contents or exception text."""
    mismatches: list[str] = []
    if expected.get("migrations") != actual.get("migrations"):
        mismatches.append("MIGRATION_SET_MISMATCH")
    if expected.get("tables") != actual.get("tables"):
        mismatches.append("TABLE_COUNT_MISMATCH")

    expected_logs = expected.get("action_logs", {})
    actual_logs = actual.get("action_logs", {})
    if expected_logs.get("by_status") != actual_logs.get("by_status"):
        mismatches.append("ACTION_LOG_STATUS_POINTS_MISMATCH")
    if expected_logs.get("attribution") != actual_logs.get("attribution"):
        mismatches.append("ACTION_LOG_ATTRIBUTION_MISMATCH")
    if expected_logs.get("history_fingerprint") != actual_logs.get(
        "history_fingerprint"
    ):
        mismatches.append("ACTION_LOG_HISTORY_MISMATCH")
    if expected_logs.get("fk_orphans") != actual_logs.get("fk_orphans"):
        mismatches.append("ACTION_LOG_FK_MISMATCH")

    expected_markers = expected.get("markers", {})
    actual_markers = actual.get("markers", {})
    if expected_markers.get("pre_id") != actual_markers.get("pre_id"):
        mismatches.append("BASELINE_PRE_MARKER_MISMATCH")
    if expected_markers.get("post_id") != actual_markers.get("post_id"):
        mismatches.append("BASELINE_POST_MARKER_MISMATCH")
    if not actual_markers.get("pre_present"):
        mismatches.append("PRE_MARKER_MISSING")
    if actual_markers.get("post_present"):
        mismatches.append("POST_MARKER_PRESENT")
    return mismatches


def _read_snapshot(
    timeout: float,
    expected: dict[str, Any] | None,
    pre_marker: str | None,
    post_marker: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Capture one coherent read-only snapshot and optional comparison codes."""
    with _deadline(timeout):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SET TRANSACTION READ ONLY")
                _set_transaction_bounds(cursor, timeout)
            if expected is not None:
                markers = expected["markers"]
                actual = _snapshot(str(markers["pre_id"]), str(markers["post_id"]))
                _validate_snapshot(actual, "El estado actual")
                return actual, _compare(expected, actual)
            actual = _snapshot(str(pre_marker), str(post_marker))
            _validate_snapshot(actual, "El estado actual")
            return actual, []


def _parse_mode(
    options: dict[str, object],
) -> tuple[Path | None, Path | None, str | None, str | None]:
    """Validate mode flags and return normalized paths and marker UUIDs."""
    baseline_path: Path | None = options.get("baseline")  # type: ignore[assignment]
    write_path: Path | None = options.get("write_baseline")  # type: ignore[assignment]
    pre_marker = options.get("pre_marker_id")
    post_marker = options.get("post_marker_id")
    if write_path is not None:
        if not pre_marker or not post_marker:
            raise CommandError(
                "--write-baseline requiere --pre-marker-id y --post-marker-id."
            )
        pre_id = _marker_id(str(pre_marker), "--pre-marker-id")
        post_id = _marker_id(str(post_marker), "--post-marker-id")
        if pre_id == post_id:
            raise CommandError("Los marcadores sintéticos deben ser distintos.")
        return baseline_path, write_path, pre_id, post_id
    if pre_marker is not None or post_marker is not None:
        raise CommandError("Los marcadores solo se indican al crear el baseline.")
    return baseline_path, write_path, None, None


def _write_baseline(path: Path, snapshot: dict[str, Any]) -> None:
    """Create a private baseline without replacing existing evidence."""
    if not snapshot["markers"]["pre_present"] or snapshot["markers"]["post_present"]:
        raise CommandError(
            "RECOVERY_VERIFY ERROR\ndiagnostico: MARKER_BASELINE_INVALID\n"
            "detalle: El baseline requiere marcador previo presente y posterior ausente."
        )
    try:
        payload = (
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(path, 0o600)
    except OSError:
        raise CommandError(
            "RECOVERY_VERIFY ERROR\ndiagnostico: BASELINE_WRITE_FAILURE\n"
            "detalle: No se pudo escribir el baseline solicitado."
        ) from None


class Command(BaseCommand):
    """Capture or compare a bounded, read-only T8 recovery integrity baseline."""

    help = "Captura o compara un baseline de integridad de recuperación PostgreSQL."

    def add_arguments(self, parser: CommandParser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--write-baseline",
            type=Path,
            help="Escribe un baseline JSON metadata-only en esta ruta.",
        )
        mode.add_argument(
            "--baseline",
            type=Path,
            help="Compara la base actual con este baseline JSON.",
        )
        parser.add_argument(
            "--pre-marker-id",
            help="ID sintético de ActionLog que debe sobrevivir a la recuperación.",
        )
        parser.add_argument(
            "--post-marker-id",
            help="ID sintético de ActionLog creado después de T; debe faltar en la recuperación.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="Límite total en segundos (0.1–60; predeterminado: 10).",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        timeout = float(options["timeout"])
        if not 0.1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise CommandError("--timeout debe estar entre 0.1 y 60 segundos.")

        typed_options = dict(options)
        baseline_path, write_path, pre_marker, post_marker = _parse_mode(typed_options)

        expected: dict[str, Any] | None = None
        if baseline_path is not None:
            expected = _load_baseline(baseline_path)

        bounded_options, original_options = _set_bounded_options(timeout)
        try:
            connection.close()
            actual, mismatches = _read_snapshot(
                timeout, expected, pre_marker, post_marker
            )
        except CommandError:
            raise
        except SmokeDeadlineExceededError:
            raise CommandError(
                "RECOVERY_VERIFY ERROR\ndiagnostico: TIMEOUT\n"
                "detalle: La verificación no terminó dentro del límite configurado."
            ) from None
        except DatabaseError as error:
            if _is_missing_schema(error):
                raise CommandError(
                    "RECOVERY_VERIFY ERROR\ndiagnostico: MISSING_MIGRATIONS\n"
                    "detalle: La conexión funciona, pero falta una tabla o columna del esquema requerido."
                ) from None
            if isinstance(error, OperationalError):
                raise CommandError(
                    "RECOVERY_VERIFY ERROR\ndiagnostico: CONNECTION_FAILURE\n"
                    "detalle: No se pudo abrir o mantener la conexión PostgreSQL dentro del límite configurado."
                ) from None
            raise CommandError(
                "RECOVERY_VERIFY ERROR\ndiagnostico: READ_FAILURE\n"
                "detalle: No se pudo consultar el esquema o el historial dentro del límite."
            ) from None
        except Exception:  # noqa: BLE001 - keep driver and filesystem details out of output
            raise CommandError(
                "RECOVERY_VERIFY ERROR\ndiagnostico: RECOVERY_CHECK_FAILURE\n"
                "detalle: No se pudo completar la verificación de recuperación."
            ) from None
        finally:
            _restore_bounded_options(bounded_options, original_options)

        if write_path is not None:
            _write_baseline(write_path, actual)
            self.stdout.write("RECOVERY_VERIFY BASELINE_OK")
            self.stdout.write(
                "migrations: captured; tables: captured; action_logs: captured"
            )
            self.stdout.write("markers: pre=present; post=absent")
            return

        if mismatches:
            raise CommandError(
                "RECOVERY_VERIFY ERROR\n"
                "diagnostico: " + ",".join(mismatches) + "\n"
                "detalle: El estado recuperado no coincide con el baseline; revise la evidencia sin exponer datos."
            )

        self.stdout.write("RECOVERY_VERIFY OK")
        self.stdout.write("migrations: match; tables: match; action_logs: match")
        self.stdout.write("markers: pre=present; post=absent")
