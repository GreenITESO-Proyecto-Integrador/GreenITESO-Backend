"""Load approved synthetic demo data into only the shared Neon dev database."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from green_iteso.accounts.management.commands.bootstrap_dev import (
    parse_as_of,
    seed_demo_data,
)
from green_iteso.actions.management.commands.load_catalog import (
    CatalogData,
    load_catalog_content,
    load_catalog_data,
)
from green_iteso.settings.neon_endpoints import canonical_neon_host

APPROVED_CATALOG = (
    Path(__file__).resolve().parents[3]
    / "actions"
    / "fixtures"
    / "catalog_approved.json"
)
APPROVED_CATALOG_SHA256_ENV = "NEON_DEV_APPROVED_CATALOG_SHA256"


def load_approved_catalog() -> CatalogData:
    """Require a Product-approved fixture pinned by its dev-environment digest."""
    expected_digest = os.environ.get(APPROVED_CATALOG_SHA256_ENV, "").strip().lower()
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise CommandError(
            f"{APPROVED_CATALOG_SHA256_ENV} must contain the approved fixture SHA-256."
        )
    try:
        contents = APPROVED_CATALOG.read_bytes()
    except OSError as error:
        raise CommandError(
            f"Could not read the canonical approved catalog {APPROVED_CATALOG}: {error}"
        ) from error
    actual_digest = hashlib.sha256(contents).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise CommandError(
            "The canonical approved catalog does not match its approved SHA-256."
        )
    return load_catalog_content(
        contents, source=str(APPROVED_CATALOG), expected_status="APPROVED"
    )


def ensure_neon_dev_target() -> None:
    """Refuse every target except the deployed, pooled Neon dev app role."""
    if os.environ.get("DJANGO_ENV") != "dev" or not settings.DEPLOYED:
        raise CommandError(
            "seed_neon_dev requires DJANGO_ENV=dev and DJANGO_DEPLOYED=true."
        )
    if settings.CONNECTION_ROLE != "app":
        raise CommandError("seed_neon_dev requires the pooled app connection role.")

    database = settings.DATABASES["default"]
    if str(database.get("HOST", "")).lower() != canonical_neon_host("dev", pooled=True):
        raise CommandError("seed_neon_dev requires the canonical Neon dev pooler host.")
    if str(database.get("USER", "")) != "greeniteso_dev_app":
        raise CommandError("seed_neon_dev requires the greeniteso_dev_app role.")
    options = database.get("OPTIONS", {})
    if not isinstance(options, dict) or options.get("sslmode") != "verify-full":
        raise CommandError("seed_neon_dev requires verified TLS for Neon dev.")


def ensure_tls_connection() -> None:
    """Verify the actual database connection is encrypted before writes."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            row = cursor.fetchone()
    except DatabaseError:
        raise CommandError(
            "Cannot verify the Neon dev TLS connection; refusing shared seed writes."
        ) from None
    if row is None or row[0] is not True:
        raise CommandError("seed_neon_dev requires an encrypted PostgreSQL connection.")


class Command(BaseCommand):
    """Seed approved synthetic demo data into Neon dev, never other branches."""

    help = "Load approved demo data into the explicitly confirmed Neon dev database."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--as-of",
            dest="as_of",
            help="Aware ISO-8601 anchor for campaign dates (default: current time).",
        )
        parser.add_argument(
            "--confirm-target",
            help="Required explicit target acknowledgement: dev.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        if options.get("confirm_target") != "dev":
            raise CommandError(
                "Shared seed writes require the explicit --confirm-target dev option."
            )
        ensure_neon_dev_target()
        catalog = load_approved_catalog()
        as_of: datetime = parse_as_of(options.get("as_of"))
        ensure_tls_connection()

        with transaction.atomic():
            load_catalog_data(catalog)
            result = seed_demo_data(catalog, as_of, shared_dev=True)

        return (
            f"Seeded Neon dev demo: {result.user_count} student users "
            f"({result.user_created} created), {result.private_clan_count} private clans, "
            f"{result.created_logs} action logs, {result.mission_count} missions, "
            f"campaign_created={result.campaign_created}."
        )
