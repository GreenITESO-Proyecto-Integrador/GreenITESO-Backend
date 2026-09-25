"""Create and compare a read-only recovery integrity baseline."""

from __future__ import annotations

import hmac
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

BASELINE_VERSION = 3
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


def _table_integrity() -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    """Fingerprint complete core-table contents without retaining row values."""
    counts: dict[str, int] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    for model in TABLE_MODELS:
        field_names = [field.attname for field in model._meta.concrete_fields]
        manager = Clan.all_objects if model is Clan else model.objects
        rows = manager.values_list(*field_names).order_by("pk").iterator()
        digest = sha256()
        count = 0
        for row in rows:
            encoded = json.dumps(
                row,
                ensure_ascii=True,
                default=str,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            digest.update(encoded)
            digest.update(b"\n")
            count += 1
        table_name = model._meta.db_table
        counts[table_name] = count
        fingerprints[table_name] = {
            "algorithm": "sha256",
            "count": count,
            "digest": digest.hexdigest(),
        }
    return counts, fingerprints


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


def _opaque_identifier(value: object) -> str:
    """Avoid storing a direct ID; the deterministic hash is pseudonymous, not anonymous."""
    return sha256(str(value).encode("utf-8")).hexdigest()


def _attribution_totals() -> list[dict[str, Any]]:
    """Summarize frozen clan attribution using IDs and aggregate values only."""
    rows = (
        ActionLog.objects.values("institutional_clan_id", "credited_private_clan_id")
        .annotate(count=Count("pk"), points=Coalesce(Sum("points_awarded"), Value(0)))
        .order_by("institutional_clan_id", "credited_private_clan_id")
    )
    return [
        {
            "institutional_clan_fingerprint": _opaque_identifier(
                row["institutional_clan_id"]
            ),
            "credited_private_clan_fingerprint": (
                _opaque_identifier(row["credited_private_clan_id"])
                if row["credited_private_clan_id"] is not None
                else None
            ),
            "count": int(row["count"]),
            "points": int(row["points"] or 0),
        }
        for row in rows
    ]


def _quoted_table(model: Any) -> str:
    """Quote a table name declared by a trusted Django model."""
    return connection.ops.quote_name(model._meta.db_table)


def _foreign_key_fields() -> list[tuple[Any, Any]]:
    """Return every concrete FK/one-to-one relation for fingerprinted models."""
    return [
        (model, field)
        for model in TABLE_MODELS
        for field in model._meta.concrete_fields
        if field.is_relation and (field.many_to_one or field.one_to_one)
    ]


def _fk_orphans() -> dict[str, int]:
    """Check every concrete FK in the fingerprinted models without row payloads."""
    checks: dict[str, str] = {}
    for model, field in _foreign_key_fields():
        target_model = field.remote_field.model
        source_table = _quoted_table(model)
        target_table = _quoted_table(target_model)
        source_column = connection.ops.quote_name(field.column)
        target_column = connection.ops.quote_name(field.target_field.column)
        nullable_guard = (
            f"source.{source_column} IS NOT NULL AND " if field.null else ""
        )
        check_name = f"{model._meta.label}.{field.name}"
        checks[check_name] = f"""
            SELECT COUNT(*) FROM {source_table} AS source
            LEFT JOIN {target_table} AS target
              ON target.{target_column} = source.{source_column}
            WHERE {nullable_guard}target.{target_column} IS NULL
        """
    result: dict[str, int] = {}
    with connection.cursor() as cursor:
        for name, query in checks.items():
            cursor.execute(query)
            result[name] = int(cursor.fetchone()[0])
    return result


def _marker_fingerprint(value: str, key: bytes) -> str:
    """Return a keyed, non-reversible fingerprint for a marker identifier."""
    marker = UUID(value)
    return hmac.new(key, b"db-recovery-marker:v1:" + marker.bytes, sha256).hexdigest()


def _marker_presence(pre_id: str, post_id: str, key: bytes) -> dict[str, Any]:
    """Record keyed marker fingerprints and presence, never marker IDs."""
    return {
        "pre_fingerprint": _marker_fingerprint(pre_id, key),
        "post_fingerprint": _marker_fingerprint(post_id, key),
        "pre_present": ActionLog.objects.filter(pk=pre_id).exists(),
        "post_present": ActionLog.objects.filter(pk=post_id).exists(),
    }


def _snapshot(pre_id: str, post_id: str, marker_key: bytes) -> dict[str, Any]:
    """Build the complete metadata-only recovery snapshot."""
    applied = _migration_set()
    table_counts, table_fingerprints = _table_integrity()
    return {
        "format": BASELINE_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "migrations": applied,
        "pending_code_migrations": _pending_code_migrations(applied),
        "tables": table_counts,
        "table_fingerprints": table_fingerprints,
        "action_logs": {
            "by_status": _status_totals(),
            "attribution": _attribution_totals(),
            "history_fingerprint": table_fingerprints[ActionLog._meta.db_table],
            "fk_orphans": _fk_orphans(),
        },
        "markers": _marker_presence(pre_id, post_id, marker_key),
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
    _validate_snapshot(value, "El baseline", require_clean_fks=True)
    return value


def _validate_snapshot(
    snapshot: dict[str, Any], label: str, *, require_clean_fks: bool = False
) -> None:
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
    expected_table_names = {model._meta.db_table for model in TABLE_MODELS}
    if (
        not isinstance(tables, dict)
        or set(tables) != expected_table_names
        or any(not _is_nonnegative_integer(value) for value in tables.values())
    ):
        raise CommandError(f"{label} no contiene conteos de tablas válidos.")
    fingerprints = snapshot.get("table_fingerprints")
    if (
        not isinstance(fingerprints, dict)
        or set(fingerprints) != expected_table_names
        or any(
            not _is_valid_fingerprint(value) or value["count"] != tables[table_name]
            for table_name, value in fingerprints.items()
        )
    ):
        raise CommandError(f"{label} no contiene las huellas completas de tablas.")

    markers = snapshot.get("markers")
    if not isinstance(markers, dict):
        raise CommandError(f"{label} debe incluir los dos marcadores sintéticos.")
    expected_marker_keys = {
        "pre_fingerprint",
        "post_fingerprint",
        "pre_present",
        "post_present",
    }
    if (
        set(markers) != expected_marker_keys
        or not _is_valid_digest(markers.get("pre_fingerprint"))
        or not _is_valid_digest(markers.get("post_fingerprint"))
        or markers.get("pre_fingerprint") == markers.get("post_fingerprint")
        or not isinstance(markers.get("pre_present"), bool)
        or not isinstance(markers.get("post_present"), bool)
    ):
        raise CommandError(f"{label} no contiene fingerprints de marcadores válidos.")

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
    expected_fk_names = {
        f"{model._meta.label}.{field.name}" for model, field in _foreign_key_fields()
    }
    if (
        not isinstance(fk_orphans, dict)
        or set(fk_orphans) != expected_fk_names
        or any(not _is_nonnegative_integer(value) for value in fk_orphans.values())
    ):
        raise CommandError(
            f"{label} contiene checks FK omitidos o conteos inválidos; "
            "no es evidencia íntegra."
        )
    if require_clean_fks and any(
        not _is_zero_integer(value) for value in fk_orphans.values()
    ):
        raise CommandError(
            f"{label} contiene referencias foráneas huérfanas; no es evidencia íntegra."
        )
    history = action_logs.get("history_fingerprint")
    if (
        not _is_valid_fingerprint(history)
        or history["count"] != tables[ActionLog._meta.db_table]
    ):
        raise CommandError(f"{label} no contiene la huella histórica requerida.")


def _is_nonnegative_integer(value: object) -> bool:
    """Accept JSON counts while excluding booleans from the integer type."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_zero_integer(value: object) -> bool:
    """Accept only a real integer zero for an orphan check."""
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _is_valid_fingerprint(value: object) -> bool:
    """Validate the public shape of a metadata-only table digest."""
    if not isinstance(value, dict):
        return False
    digest = value.get("digest")
    return (
        value.get("algorithm") == "sha256"
        and _is_nonnegative_integer(value.get("count"))
        and _is_valid_digest(digest)
    )


def _is_valid_digest(value: object) -> bool:
    """Validate a lowercase SHA-256/HMAC-SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return stable mismatch codes, never row contents or exception text."""
    mismatches: list[str] = []
    if expected.get("migrations") != actual.get("migrations"):
        mismatches.append("MIGRATION_SET_MISMATCH")
    if expected.get("tables") != actual.get("tables"):
        mismatches.append("TABLE_COUNT_MISMATCH")
    if expected.get("table_fingerprints") != actual.get("table_fingerprints"):
        mismatches.append("TABLE_CONTENT_MISMATCH")

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
        mismatches.append("FK_INTEGRITY_MISMATCH")

    expected_markers = expected.get("markers", {})
    actual_markers = actual.get("markers", {})
    if expected_markers.get("pre_fingerprint") != actual_markers.get("pre_fingerprint"):
        mismatches.append("BASELINE_PRE_MARKER_MISMATCH")
    if expected_markers.get("post_fingerprint") != actual_markers.get(
        "post_fingerprint"
    ):
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
    marker_key: bytes,
) -> tuple[dict[str, Any], list[str]]:
    """Capture one coherent read-only snapshot and optional comparison codes."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("SET TRANSACTION READ ONLY")
            _set_transaction_bounds(cursor, timeout)
        if expected is not None:
            actual = _snapshot(str(pre_marker), str(post_marker), marker_key)
            _validate_snapshot(actual, "El estado actual")
            return actual, _compare(expected, actual)
        actual = _snapshot(str(pre_marker), str(post_marker), marker_key)
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
    if not pre_marker or not post_marker:
        mode = "--write-baseline" if write_path is not None else "--baseline"
        raise CommandError(f"{mode} requiere --pre-marker-id y --post-marker-id.")
    pre_id = _marker_id(str(pre_marker), "--pre-marker-id")
    post_id = _marker_id(str(post_marker), "--post-marker-id")
    if pre_id == post_id:
        raise CommandError("Los marcadores sintéticos deben ser distintos.")
    return baseline_path, write_path, pre_id, post_id


def _marker_hmac_key() -> bytes:
    """Load a drill-local key that is deliberately excluded from the baseline."""
    value = os.environ.get("DB_RECOVERY_MARKER_HMAC_KEY", "")
    key = value.encode("utf-8")
    if len(key) < 32:
        raise CommandError(
            "DB_RECOVERY_MARKER_HMAC_KEY debe contener al menos 32 bytes."
        )
    return key


def _write_baseline(path: Path, snapshot: dict[str, Any]) -> None:
    """Create a private baseline without replacing existing evidence."""
    fk_orphans = snapshot["action_logs"]["fk_orphans"]
    if any(not _is_zero_integer(value) for value in fk_orphans.values()):
        raise CommandError(
            "RECOVERY_VERIFY ERROR\ndiagnostico: BASELINE_FK_INTEGRITY_INVALID\n"
            "detalle: El baseline requiere cero referencias foráneas huérfanas."
        )
    if not snapshot["markers"]["pre_present"] or snapshot["markers"]["post_present"]:
        raise CommandError(
            "RECOVERY_VERIFY ERROR\ndiagnostico: MARKER_BASELINE_INVALID\n"
            "detalle: El baseline requiere marcador previo presente y posterior ausente."
        )
    created = False
    try:
        payload = (
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(path, 0o600)
    except BaseException as error:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if not isinstance(error, OSError):
            raise
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

        bounded_options: dict[str, object] | None = None
        original_options: dict[str, object] = {}
        try:
            marker_key = _marker_hmac_key()
            with _deadline(timeout):
                expected: dict[str, Any] | None = None
                if baseline_path is not None:
                    expected = _load_baseline(baseline_path)
                bounded_options, original_options = _set_bounded_options(timeout)
                try:
                    connection.close()
                    actual, mismatches = _read_snapshot(
                        timeout,
                        expected,
                        pre_marker,
                        post_marker,
                        marker_key,
                    )
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
                            "detalle: El estado recuperado no coincide con el baseline; "
                            "revise la evidencia sin exponer datos."
                        )
                    self.stdout.write("RECOVERY_VERIFY OK")
                    self.stdout.write(
                        "migrations: match; tables: match; action_logs: match"
                    )
                    self.stdout.write("markers: pre=present; post=absent")
                finally:
                    if bounded_options is not None:
                        _restore_bounded_options(bounded_options, original_options)
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
