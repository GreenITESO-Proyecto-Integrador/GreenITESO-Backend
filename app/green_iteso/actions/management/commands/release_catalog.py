"""Release the pinned approved reference catalog to a shared environment."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import transaction

from green_iteso.accounts.models import Clan
from green_iteso.actions.management.commands.load_catalog import (
    CatalogData,
    load_catalog_content,
    load_catalog_data,
    stable_reference_id,
)
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.core.database import require_connection_tls
from green_iteso.settings.neon_endpoints import canonical_neon_host

APPROVED_CATALOG = (
    Path(__file__).resolve().parents[2] / "fixtures" / "catalog_approved_v1.json"
)
APPROVED_CATALOG_SHA256_ENV = {
    "dev": "NEON_DEV_APPROVED_CATALOG_SHA256",
    "staging": "NEON_STAGING_APPROVED_CATALOG_SHA256",
    "production": "NEON_PRODUCTION_APPROVED_CATALOG_SHA256",
}
APPROVED_APP_ROLES = {
    "dev": "greeniteso_dev_app",
    "staging": "greeniteso_staging_app",
    "production": "greeniteso_production_app",
}


def load_approved_catalog(environment: str) -> CatalogData:
    """Read only the fixed versioned artifact, pinned outside the repository."""
    pin_name = APPROVED_CATALOG_SHA256_ENV[environment]
    expected = os.environ.get(pin_name, "").strip().lower()
    if len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise CommandError(f"{pin_name} must contain the approved fixture SHA-256.")
    try:
        content = APPROVED_CATALOG.read_bytes()
    except OSError as error:
        raise CommandError(
            f"Could not read canonical approved catalog: {error}"
        ) from error
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise CommandError(
            "Canonical approved catalog does not match its approved SHA-256."
        )
    return load_catalog_content(
        content, source=str(APPROVED_CATALOG), expected_status="APPROVED"
    )


def ensure_release_target(environment: str) -> None:
    """Require the explicitly selected deployed branch, app role, and verified TLS."""
    if os.environ.get("DJANGO_ENV") != environment or not settings.DEPLOYED:
        raise CommandError(
            "release_catalog requires matching DJANGO_ENV and DJANGO_DEPLOYED=true."
        )
    if settings.CONNECTION_ROLE != "app":
        raise CommandError("release_catalog requires the pooled app connection role.")
    database = settings.DATABASES["default"]
    expected_host = canonical_neon_host(environment, pooled=True)
    if str(database.get("HOST", "")).lower() != expected_host:
        raise CommandError(
            f"release_catalog requires the canonical Neon {environment} branch."
        )
    expected_role = APPROVED_APP_ROLES[environment]
    if str(database.get("USER", "")) != expected_role:
        raise CommandError(
            f"release_catalog requires the {expected_role} database role."
        )
    options = database.get("OPTIONS", {})
    if not isinstance(options, dict) or options.get("sslmode") != "verify-full":
        raise CommandError("release_catalog requires sslmode=verify-full.")


def ensure_tls_connection() -> None:
    """Verify the actual client connection is encrypted before writes."""
    require_connection_tls(
        encrypted=True,
        inspection_error=(
            "Cannot verify approved catalog TLS connection; refusing shared writes."
        ),
        mismatch_error="Approved catalog release requires an encrypted connection.",
    )


def reject_existing_drift(catalog: CatalogData) -> None:
    """Allow exact reruns, but never silently overwrite existing reference rows."""
    for item in catalog.categories:
        row = ActionCategory.objects.filter(code=item["code"]).first()
        expected = (item["name"], item["description"], item["icon"])
        if row:
            actual = (row.name, row.description, row.icon)
            if (
                row.pk != stable_reference_id("category", item["code"])
                or actual != expected
            ):
                raise CommandError(
                    f"Approved catalog category {item['code']} differs from fixture."
                )
        elif ActionCategory.objects.filter(
            pk=stable_reference_id("category", item["code"])
        ).exists():
            raise CommandError("Approved catalog category identity collision.")

    for item in catalog.actions:
        row = (
            ActionMaster.objects.filter(code=item["code"])
            .select_related("category")
            .first()
        )
        expected = (
            item["name"],
            item["description"],
            item["points"],
            item["daily_limit"],
            item["validation_type"],
            item["co2_kg_factor"],
            item["water_liters_factor"],
            item["plastic_kg_factor"],
            item["is_active"],
            item["category_code"],
        )
        if row:
            actual = (
                row.name,
                row.description,
                row.points,
                row.daily_limit,
                row.validation_type,
                row.co2_kg_factor,
                row.water_liters_factor,
                row.plastic_kg_factor,
                row.is_active,
                row.category.code,
            )
            if (
                row.pk != stable_reference_id("action", item["code"])
                or actual != expected
            ):
                raise CommandError(
                    f"Approved catalog action {item['code']} differs from fixture."
                )
        elif ActionMaster.objects.filter(
            pk=stable_reference_id("action", item["code"])
        ).exists():
            raise CommandError("Approved catalog action identity collision.")

    for item in catalog.clans:
        clan_id = stable_reference_id("institutional-clan", item["key"])
        row = Clan.all_objects.filter(pk=clan_id).first()
        expected = (
            item["name"],
            f"{item['description']} Career key: {item['career']}",
            Clan.ClanType.INSTITUTIONAL,
            Clan.Privacy.PUBLIC,
        )
        if row and (
            row.deleted_at is not None
            or (row.name, row.description, row.type, row.privacy) != expected
        ):
            raise CommandError(
                f"Approved catalog clan {item['key']} differs from fixture."
            )
        if Clan.all_objects.filter(name=item["name"]).exclude(pk=clan_id).exists():
            raise CommandError(
                f"Approved catalog clan {item['key']} identity collision."
            )


class Command(BaseCommand):
    """Import only approved catalog reference rows, never synthetic activity."""

    help = "Release a pinned approved catalog to an explicitly confirmed environment."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-target",
            choices=tuple(APPROVED_CATALOG_SHA256_ENV),
            required=True,
            help="Explicit target acknowledgement: dev, staging, or production.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        environment = options["confirm_target"]
        ensure_release_target(environment)
        catalog = load_approved_catalog(environment)
        ensure_tls_connection()

        with transaction.atomic():
            reject_existing_drift(catalog)
            created, unchanged = load_catalog_data(catalog)
            # Recheck after get_or_create: a concurrent release may have won a
            # unique-key race between our preflight and the actual inserts.
            reject_existing_drift(catalog)

        return (
            f"Released approved catalog v{catalog.version}: {created} created, "
            f"{unchanged} unchanged."
        )
