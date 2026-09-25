"""Idempotent import for the explicitly provisional action catalog."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from green_iteso.accounts.models import Clan
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.core.database import require_connection_tls

CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,49}$")
REFERENCE_NAMESPACE = uuid.UUID("b486b2ae-0e5b-4a78-9f6f-2f8df2c53f5e")
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[2] / "fixtures" / "catalog_draft.json"
)
LOCAL_DATABASE_HOSTS = {"127.0.0.1", "localhost", "::1", "db"}


@dataclass(frozen=True)
class CatalogData:
    """Validated catalog payload, ready to be written in one transaction."""

    version: int
    categories: tuple[dict[str, Any], ...]
    actions: tuple[dict[str, Any], ...]
    clans: tuple[dict[str, Any], ...]


def stable_reference_id(kind: str, code: str) -> uuid.UUID:
    """Return an environment-independent ID for a reference key."""
    return uuid.uuid5(REFERENCE_NAMESPACE, f"{kind}:{code}")


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CommandError(
            f"{field} must be a nonblank string of at most {maximum} characters."
        )
    return value.strip()


def _code(value: object, field: str) -> str:
    code = _text(value, field, maximum=50)
    if not CODE_PATTERN.fullmatch(code):
        raise CommandError(f"{field} must match {CODE_PATTERN.pattern!r}.")
    return code


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CommandError(f"{field} must be a positive integer.")
    return value


def _factor(value: object, field: str) -> Decimal:
    try:
        factor = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CommandError(f"{field} must be a nonnegative decimal.") from None
    if (
        not factor.is_finite()
        or factor < 0
        or factor.as_tuple().exponent < -3
        or factor >= Decimal("1000000000")
    ):
        raise CommandError(
            f"{field} must be a finite nonnegative decimal with at most 3 places."
        )
    return factor.quantize(Decimal("0.001"))


def _payload_parts(
    payload: object, *, expected_status: str
) -> tuple[int, list[object], list[object], list[object]]:
    """Validate envelope fields and return the three raw collections."""
    if not isinstance(payload, dict):
        raise CommandError("Catalog payload must be a JSON object.")
    if expected_status not in {"DRAFT", "APPROVED"}:
        raise CommandError("Catalog status must be DRAFT or APPROVED.")
    if payload.get("status") != expected_status:
        raise CommandError(
            f"Only status={expected_status} catalog data is accepted by this importer."
        )
    if expected_status == "APPROVED":
        approval = payload.get("approval")
        if not isinstance(approval, dict):
            raise CommandError("APPROVED catalog data requires approval metadata.")
        _text(approval.get("approved_by"), "approval.approved_by", maximum=150)
        _text(approval.get("reference"), "approval.reference", maximum=200)
        approved_at = _text(
            approval.get("approved_at"), "approval.approved_at", maximum=40
        )
        try:
            approval_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise CommandError(
                "approval.approved_at must be an aware ISO-8601 timestamp."
            ) from error
        if approval_time.utcoffset() is None:
            raise CommandError("approval.approved_at must include a timezone offset.")
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CommandError("schema_version must be a positive integer.")

    raw_categories = payload.get("categories")
    raw_actions = payload.get("actions")
    raw_clans = payload.get("institutional_clans")
    if not all(
        isinstance(entries, list)
        for entries in (raw_categories, raw_actions, raw_clans)
    ):
        raise CommandError(
            "categories, actions, and institutional_clans must be arrays."
        )

    return version, raw_categories, raw_actions, raw_clans


def _validate_categories(
    raw_categories: list[object],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Validate category rows and return their stable code index."""
    category_codes: set[str] = set()
    categories: list[dict[str, Any]] = []
    for index, item in enumerate(raw_categories):
        if not isinstance(item, dict):
            raise CommandError(f"categories[{index}] must be an object.")
        code = _code(item.get("code"), f"categories[{index}].code")
        if code in category_codes:
            raise CommandError(f"Duplicate category code: {code}.")
        category_codes.add(code)
        categories.append(
            {
                "code": code,
                "name": _text(
                    item.get("name"), f"categories[{index}].name", maximum=100
                ),
                "description": str(item.get("description", "")).strip(),
                "icon": str(item.get("icon", "")).strip(),
            }
        )
    return categories, category_codes


def _validate_actions(
    raw_actions: list[object], category_codes: set[str]
) -> list[dict[str, Any]]:
    """Validate action rows and category references."""
    action_codes: set[str] = set()
    actions: list[dict[str, Any]] = []
    valid_types = {choice for choice, _label in ActionMaster.ValidationType.choices}
    for index, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            raise CommandError(f"actions[{index}] must be an object.")
        code = _code(item.get("code"), f"actions[{index}].code")
        if code in action_codes:
            raise CommandError(f"Duplicate action code: {code}.")
        action_codes.add(code)
        category_code = _code(
            item.get("category_code"), f"actions[{index}].category_code"
        )
        if category_code not in category_codes:
            raise CommandError(
                f"actions[{index}] references unknown category code {category_code!r}."
            )
        validation_type = item.get("validation_type")
        if validation_type not in valid_types:
            raise CommandError(
                f"actions[{index}].validation_type must be one of {sorted(valid_types)}."
            )
        is_active = item.get("is_active", True)
        if not isinstance(is_active, bool):
            raise CommandError(f"actions[{index}].is_active must be a boolean.")
        actions.append(
            {
                "code": code,
                "category_code": category_code,
                "name": _text(item.get("name"), f"actions[{index}].name", maximum=150),
                "description": _text(
                    item.get("description"),
                    f"actions[{index}].description",
                    maximum=10000,
                ),
                "points": _positive_int(item.get("points"), f"actions[{index}].points"),
                "daily_limit": _positive_int(
                    item.get("daily_limit"), f"actions[{index}].daily_limit"
                ),
                "validation_type": validation_type,
                "co2_kg_factor": _factor(
                    item.get("co2_kg_factor", 0), f"actions[{index}].co2_kg_factor"
                ),
                "water_liters_factor": _factor(
                    item.get("water_liters_factor", 0),
                    f"actions[{index}].water_liters_factor",
                ),
                "plastic_kg_factor": _factor(
                    item.get("plastic_kg_factor", 0),
                    f"actions[{index}].plastic_kg_factor",
                ),
                "is_active": is_active,
            }
        )
    return actions


def _validate_clans(
    raw_clans: list[object], *, require_details: bool = False
) -> list[dict[str, Any]]:
    """Validate institutional clan reference rows."""
    clan_keys: set[str] = set()
    clans: list[dict[str, Any]] = []
    for index, item in enumerate(raw_clans):
        if not isinstance(item, dict):
            raise CommandError(f"institutional_clans[{index}] must be an object.")
        key = _code(item.get("key"), f"institutional_clans[{index}].key")
        if key in clan_keys:
            raise CommandError(f"Duplicate institutional clan key: {key}.")
        clan_keys.add(key)
        if (
            item.get("type") != Clan.ClanType.INSTITUTIONAL
            or item.get("privacy") != Clan.Privacy.PUBLIC
        ):
            raise CommandError(
                f"institutional_clans[{index}] must be an institutional public clan."
            )
        clans.append(
            {
                "key": key,
                "name": _text(
                    item.get("name"), f"institutional_clans[{index}].name", maximum=100
                ),
                "description": (
                    _text(
                        item.get("description"),
                        f"institutional_clans[{index}].description",
                        maximum=10000,
                    )
                    if require_details
                    else str(item.get("description", "")).strip()
                ),
                "career": _text(
                    item.get("career")
                    if require_details
                    else item.get("career", "Draft career"),
                    f"institutional_clans[{index}].career",
                    maximum=150,
                ),
            }
        )
    return clans


def validate_catalog(payload: object, *, expected_status: str = "DRAFT") -> CatalogData:
    """Validate every field before opening a write transaction."""
    version, raw_categories, raw_actions, raw_clans = _payload_parts(
        payload, expected_status=expected_status
    )
    categories, category_codes = _validate_categories(raw_categories)
    actions = _validate_actions(raw_actions, category_codes)
    clans = _validate_clans(raw_clans, require_details=expected_status == "APPROVED")
    return CatalogData(version, tuple(categories), tuple(actions), tuple(clans))


def load_catalog_content(
    content: str | bytes,
    *,
    source: str,
    expected_status: str = "DRAFT",
) -> CatalogData:
    """Parse and validate catalog content before any database mutation."""
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CommandError(f"Could not parse catalog file {source}: {error}") from error
    return validate_catalog(payload, expected_status=expected_status)


def load_catalog_file(path: Path, *, expected_status: str = "DRAFT") -> CatalogData:
    """Read and validate a JSON catalog before any database mutation."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise CommandError(f"Could not read catalog file {path}: {error}") from error
    return load_catalog_content(
        content, source=str(path), expected_status=expected_status
    )


def ensure_local_database() -> None:
    """Keep local-only DRAFT writes off cloud endpoints and TLS tunnels."""
    host = str(settings.DATABASES["default"].get("HOST", "")).lower()
    if host not in LOCAL_DATABASE_HOSTS:
        raise CommandError(
            "The DRAFT catalog requires a local PostgreSQL host; refusing a non-local database target."
        )
    require_connection_tls(
        encrypted=False,
        inspection_error=(
            "Cannot verify an unencrypted local PostgreSQL connection; "
            "refusing a shared database target."
        ),
        mismatch_error=(
            "The DRAFT catalog requires an unencrypted local PostgreSQL connection; "
            "refusing a shared database target."
        ),
    )


class Command(BaseCommand):
    """Import draft reference rows without replacing admin edits."""

    help = "Import the provisional DRAFT action catalog and institutional clans."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--input",
            type=Path,
            default=DEFAULT_CATALOG,
            help="Path to a DRAFT catalog JSON file.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        catalog = load_catalog_file(options["input"])
        if settings.DEPLOYED:
            raise CommandError(
                "A DRAFT catalog is local-only and cannot be loaded into a deployed environment."
            )
        ensure_local_database()

        with transaction.atomic():
            created, preserved = load_catalog_data(catalog)

        message = (
            f"Loaded DRAFT catalog v{catalog.version}: {len(catalog.categories)} categories, "
            f"{len(catalog.actions)} actions, {len(catalog.clans)} institutional clans "
            f"({created} created, {preserved} preserved)."
        )
        return message


def load_catalog_data(catalog: CatalogData) -> tuple[int, int]:
    """Import validated catalog rows inside the caller's transaction."""
    categories: dict[str, ActionCategory] = {}
    created = 0
    preserved = 0
    for item in catalog.categories:
        category, was_created = ActionCategory.objects.get_or_create(
            code=item["code"],
            defaults={
                "id": stable_reference_id("category", item["code"]),
                "name": item["name"],
                "description": item["description"],
                "icon": item["icon"],
            },
        )
        if not was_created and category.pk != stable_reference_id(
            "category", item["code"]
        ):
            raise CommandError(
                "Catalog category identity collision; existing row was preserved."
            )
        categories[item["code"]] = category
        created += was_created
        preserved += not was_created

    for item in catalog.actions:
        action, was_created = ActionMaster.objects.get_or_create(
            code=item["code"],
            defaults={
                "id": stable_reference_id("action", item["code"]),
                "category": categories[item["category_code"]],
                "name": item["name"],
                "description": item["description"],
                "points": item["points"],
                "daily_limit": item["daily_limit"],
                "validation_type": item["validation_type"],
                "co2_kg_factor": item["co2_kg_factor"],
                "water_liters_factor": item["water_liters_factor"],
                "plastic_kg_factor": item["plastic_kg_factor"],
                "is_active": item["is_active"],
            },
        )
        if not was_created and action.pk != stable_reference_id("action", item["code"]):
            raise CommandError(
                "Catalog action identity collision; existing row was preserved."
            )
        created += was_created
        preserved += not was_created

    for item in catalog.clans:
        clan_id = stable_reference_id("institutional-clan", item["key"])
        try:
            with transaction.atomic():
                if (
                    Clan.all_objects.filter(name=item["name"])
                    .exclude(pk=clan_id)
                    .exists()
                ):
                    raise CommandError(
                        "Catalog clan name collision; existing row was preserved."
                    )
                clan, was_created = Clan.all_objects.get_or_create(
                    pk=clan_id,
                    defaults={
                        "name": item["name"],
                        "description": f"{item['description']} Career key: {item['career']}",
                        "type": Clan.ClanType.INSTITUTIONAL,
                        "privacy": Clan.Privacy.PUBLIC,
                    },
                )
        except IntegrityError as error:
            if Clan.all_objects.filter(name=item["name"]).exclude(pk=clan_id).exists():
                raise CommandError(
                    "Catalog clan name collision; existing row was preserved."
                ) from error
            raise
        if not was_created and (
            clan.deleted_at is not None
            or clan.type != Clan.ClanType.INSTITUTIONAL
            or clan.privacy != Clan.Privacy.PUBLIC
        ):
            raise CommandError(
                "Catalog clan identity collision; existing row was preserved."
            )
        created += was_created
        preserved += not was_created

    return created, preserved
