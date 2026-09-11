"""Idempotent import for the explicitly provisional action catalog."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from green_iteso.accounts.models import Clan
from green_iteso.actions.models import ActionCategory, ActionMaster

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


def validate_catalog(payload: object) -> CatalogData:
    """Validate every field before opening a write transaction."""
    if not isinstance(payload, dict):
        raise CommandError("Catalog payload must be a JSON object.")
    if payload.get("status") != "DRAFT":
        raise CommandError(
            "Only status=DRAFT catalog data is accepted by this provisional importer."
        )
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

    action_codes: set[str] = set()
    actions: list[dict[str, Any]] = []
    valid_modes = {choice for choice, _label in ActionMaster.ValidationMode.choices}
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
        mode = item.get("validation_mode")
        if mode not in valid_modes:
            raise CommandError(
                f"actions[{index}].validation_mode must be one of {sorted(valid_modes)}."
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
                "validation_mode": mode,
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
                "description": str(item.get("description", "")).strip(),
                "career": _text(
                    item.get("career", "Draft career"),
                    f"institutional_clans[{index}].career",
                    maximum=150,
                ),
            }
        )

    return CatalogData(version, tuple(categories), tuple(actions), tuple(clans))


def load_catalog_file(path: Path) -> CatalogData:
    """Read and validate a JSON catalog before any database mutation."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"Could not read catalog file {path}: {error}") from error
    return validate_catalog(payload)


def ensure_local_database() -> None:
    """Keep local-only DRAFT writes away from shared cloud databases."""
    host = str(settings.DATABASES["default"].get("HOST", "")).lower()
    if host not in LOCAL_DATABASE_HOSTS:
        raise CommandError(
            "The DRAFT catalog requires a local PostgreSQL host; refusing a non-local database target."
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
                categories[item["code"]] = category
                created += was_created
                preserved += not was_created

            for item in catalog.actions:
                _, was_created = ActionMaster.objects.get_or_create(
                    code=item["code"],
                    defaults={
                        "id": stable_reference_id("action", item["code"]),
                        "category": categories[item["category_code"]],
                        "name": item["name"],
                        "description": item["description"],
                        "points": item["points"],
                        "daily_limit": item["daily_limit"],
                        "validation_mode": item["validation_mode"],
                        "co2_kg_factor": item["co2_kg_factor"],
                        "water_liters_factor": item["water_liters_factor"],
                        "plastic_kg_factor": item["plastic_kg_factor"],
                        "is_active": item["is_active"],
                    },
                )
                created += was_created
                preserved += not was_created

            for item in catalog.clans:
                _, was_created = Clan.objects.get_or_create(
                    pk=stable_reference_id("institutional-clan", item["key"]),
                    defaults={
                        "name": item["name"],
                        "description": f"{item['description']} Career key: {item['career']}",
                        "type": Clan.ClanType.INSTITUTIONAL,
                        "privacy": Clan.Privacy.PUBLIC,
                    },
                )
                created += was_created
                preserved += not was_created

        message = (
            f"Loaded DRAFT catalog v{catalog.version}: {len(catalog.categories)} categories, "
            f"{len(catalog.actions)} actions, {len(catalog.clans)} institutional clans "
            f"({created} created, {preserved} preserved)."
        )
        return message
