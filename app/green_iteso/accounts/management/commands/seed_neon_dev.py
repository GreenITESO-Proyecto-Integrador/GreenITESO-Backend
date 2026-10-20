"""Load pinned synthetic demo data into only the shared Neon dev database."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from django.db.utils import OperationalError

from green_iteso.accounts.management.commands.bootstrap_dev import (
    DEMO_INSTITUTIONAL_CLAN_DESCRIPTION,
    DEMO_STATUSES,
    demo_id,
    get_catalog_actions,
    parse_as_of,
    seed_demo_data,
)
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands.load_catalog import (
    CatalogData,
    load_catalog_content,
    load_catalog_data,
    stable_reference_id,
)
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.core.database import require_connection_tls
from green_iteso.settings.neon_endpoints import canonical_neon_host

DEV_SYNTHETIC_CATALOG = (
    Path(__file__).resolve().parents[3]
    / "actions"
    / "fixtures"
    / "catalog_dev_synthetic_v1.json"
)
DEV_SYNTHETIC_CATALOG_SHA256_ENV = "NEON_DEV_SYNTHETIC_CATALOG_SHA256"
DEV_CAREER_LABELS = (
    "Ingeniería en Desarrollo de Software",
    "Ingeniería en Sistemas Computacionales",
    "Ingeniería en Mecatrónica",
    "Ingeniería en Ciberseguridad",
)
LEGACY_INSTITUTIONAL_CLAN_NAMES = tuple(
    f"Demo institutional clan {index:02d}" for index in range(1, 4)
)
LEGACY_CAREER_LABELS = tuple(f"DEMO-CAREER-{index:02d}" for index in range(1, 4))
NEON_DEV_SEED_LOCK_KEY = (1196579156, 1)


def acquire_neon_dev_seed_lock() -> None:
    """Serialize shared-dev seed transactions before their first data read."""
    if not connection.in_atomic_block:
        raise CommandError("The Neon dev seed lock requires an active transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", NEON_DEV_SEED_LOCK_KEY)


def _career_clan_specs(catalog: CatalogData) -> list[tuple[dict[str, Any], Any]]:
    """Require the exact self-declared, dev-only career list before reconciling."""
    if (
        tuple(item["career"] for item in catalog.clans) != DEV_CAREER_LABELS
        or tuple(item["name"] for item in catalog.clans) != DEV_CAREER_LABELS
    ):
        raise CommandError(
            "The pinned Neon dev fixture must contain exactly the four configured career labels."
        )
    return [
        (item, stable_reference_id("institutional-clan", item["key"]))
        for item in catalog.clans
    ]


def _career_clan_map(
    catalog: CatalogData, locked_clans: dict[Any, Clan]
) -> list[tuple[dict[str, Any], Clan]]:
    """Validate career destination identities from their already locked rows."""
    result: list[tuple[dict[str, Any], Clan]] = []
    for item, clan_id in _career_clan_specs(catalog):
        clan = locked_clans.get(clan_id)
        if clan is None:
            raise CommandError(
                "A configured synthetic career clan is missing; refusing reconciliation."
            )
        expected_description = f"{item['description']} Career key: {item['career']}"
        if (
            clan.deleted_at is not None
            or clan.name != item["name"]
            or clan.description != expected_description
            or clan.type != Clan.ClanType.INSTITUTIONAL
            or clan.privacy != Clan.Privacy.PUBLIC
            or clan.created_by_id is not None
        ):
            raise CommandError(
                "A synthetic career clan differs from the pinned fixture; refusing reconciliation."
            )
        result.append((item, clan))
    return result


def _validate_legacy_clan_identities(legacy_clans: dict[Any, Clan]) -> None:
    """Validate locked v1 source clans before synthetic references are moved."""
    expected_ids = [
        demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
    ]
    if len(legacy_clans) != len(expected_ids):
        raise CommandError(
            "The shared-dev legacy seed is partial; refusing career reconciliation."
        )
    for index, clan_id in enumerate(expected_ids):
        clan = legacy_clans.get(clan_id)
        if (
            clan is None
            or clan.name != LEGACY_INSTITUTIONAL_CLAN_NAMES[index]
            or clan.description != DEMO_INSTITUTIONAL_CLAN_DESCRIPTION
            or clan.type != Clan.ClanType.INSTITUTIONAL
            or clan.privacy != Clan.Privacy.PUBLIC
            or clan.deleted_at is not None
            or clan.created_by_id is not None
        ):
            raise CommandError(
                "A legacy demo profile or clan differs from the known synthetic seed; refusing reconciliation."
            )


def _expected_demo_log_action(index: int, catalog: CatalogData) -> tuple[Any, str]:
    actions = get_catalog_actions(catalog)
    action = actions[index % len(actions)]
    status = DEMO_STATUSES[index % len(DEMO_STATUSES)]
    if status == ActionLog.Status.PENDING_AUDIT:
        photo_action = next(
            (
                candidate
                for candidate in actions
                if candidate.validation_type == ActionMaster.ValidationType.PHOTO
            ),
            None,
        )
        if photo_action is not None:
            action = photo_action
        else:
            status = ActionLog.Status.APPROVED
    return action, status


def _lock_synthetic_users(user_ids: list[Any]) -> list[User]:
    """Lock demo users after clans to block identity edits and membership inserts.

    Action submission locks User before UserProfile, but cannot run for this
    deterministic set: demo accounts have unusable passwords, no linked
    identity provider, and example.invalid addresses rejected by Microsoft
    login. Identity fields are rechecked after these locks are acquired.
    """
    try:
        return list(
            User.objects.select_for_update(nowait=True, of=("self",))
            .filter(pk__in=user_ids)
            .order_by("pk")
        )
    except OperationalError as error:
        if getattr(error.__cause__, "sqlstate", None) == "55P03":
            raise CommandError(
                "A concurrent write is holding a synthetic demo user; retry the Neon dev seed."
            ) from error
        raise


def _lock_synthetic_profiles(user_ids: list[Any]) -> list[UserProfile]:
    return list(
        UserProfile.objects.select_for_update(of=("self",))
        .filter(user_id__in=user_ids)
        .order_by("user_id", "pk")
    )


def _lock_synthetic_clans(clan_ids: list[Any]) -> dict[Any, Clan]:
    """Lock every source and destination clan, including zero-delta targets."""
    return {
        clan.pk: clan
        for clan in Clan.all_objects.select_for_update(no_key=True, of=("self",))
        .filter(pk__in=clan_ids)
        .order_by("pk")
    }


def _lock_synthetic_reconciliation_rows(
    user_ids: list[Any],
    clan_ids: list[Any],
) -> tuple[
    list[ActionLog],
    list[User],
    list[UserProfile],
    list[ClanMembership],
    dict[Any, Clan],
]:
    """Lock deterministic rows in application lock order, stably by key.

    Audit review locks ActionLog before its profile and clan totals. Lock logs
    first, then Profiles, Clans, Users (NOWAIT), and Memberships. This matches
    leadership operations (Clan→User→Membership), fails closed if an insert
    already holds a User FK lock, and serializes later inserts after the seed.
    """
    demo_log_ids = [demo_id("action-log", str(index)) for index in range(24)]
    logs = list(
        ActionLog.objects.select_for_update(of=("self",))
        .filter(pk__in=demo_log_ids)
        .order_by("pk")
    )
    profiles = _lock_synthetic_profiles(user_ids)
    # NO KEY UPDATE blocks clan edits while allowing FK KEY SHARE locks. It
    # precedes User and Membership locks to match clan-admin/leadership flows.
    clans = _lock_synthetic_clans(clan_ids)
    users = _lock_synthetic_users(user_ids)
    memberships = list(
        ClanMembership.objects.select_for_update(of=("self",))
        .filter(user_id__in=user_ids, clan__type=Clan.ClanType.INSTITUTIONAL)
        .order_by("user_id", "pk")
    )
    return logs, users, profiles, memberships, clans


def _validate_demo_logs(
    catalog: CatalogData,
    users: list[Any],
    private_clans: list[Clan],
    expected_clans: dict[Any, Clan],
    locked_logs: list[ActionLog],
) -> list[ActionLog]:
    """Verify all deterministic demo logs before changing their clan reference."""
    logs_by_id = {log.pk: log for log in locked_logs}
    if len(logs_by_id) != 24:
        raise CommandError(
            "The shared-dev demo log set is partial; refusing career reconciliation."
        )
    logs: list[ActionLog] = []
    for index in range(24):
        log = logs_by_id[demo_id("action-log", str(index))]
        user = users[index % len(users)]
        expected_action, expected_status = _expected_demo_log_action(index, catalog)
        expected_institutional = expected_clans[user.pk]
        expected_private = private_clans[index % len(private_clans)]
        expected_evidence = (
            f"demo-only/action-{index:02d}.jpg"
            if expected_action.validation_type == ActionMaster.ValidationType.PHOTO
            else ""
        )
        is_rejected = expected_status == ActionLog.Status.REJECTED
        if (
            log.idempotency_key != f"demo-action-{index:02d}"
            or log.user_id != user.pk
            or log.action_id != expected_action.pk
            or log.institutional_clan_id != expected_institutional.pk
            or log.credited_private_clan_id != expected_private.pk
            or log.status != expected_status
            or log.points_awarded != expected_action.points
            or log.co2_kg_factor_snapshot != expected_action.co2_kg_factor
            or log.water_liters_factor_snapshot != expected_action.water_liters_factor
            or log.plastic_kg_factor_snapshot != expected_action.plastic_kg_factor
            or log.campaign_id
            != (demo_id("campaign", "active") if index < 12 else None)
            or log.evidence_object_key != expected_evidence
            or log.reviewed_by_id is not None
            or (log.reviewed_at is not None) != is_rejected
            or log.rejection_reason
            != ("Synthetic system-rejected example" if is_rejected else "")
        ):
            raise CommandError(
                "A deterministic shared-dev action log was edited or has an unexpected identity; refusing reconciliation."
            )
        logs.append(log)
    return logs


def reconcile_legacy_demo_careers(catalog: CatalogData) -> bool:
    """Move only a complete, verified v1 synthetic seed onto the four career clans.

    Returns True when the known v1 dataset was reconciled. A clean database and
    an already reconciled dataset return False. Partial or edited states fail
    closed so this command never guesses whether a row is synthetic.
    """
    user_ids = [demo_id("user", str(index)) for index in range(1, 21)]
    career_specs = _career_clan_specs(catalog)
    career_clan_ids = [clan_id for _item, clan_id in career_specs]
    legacy_clan_ids = [
        demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
    ]
    reconciliation_clan_ids = sorted(set(career_clan_ids + legacy_clan_ids), key=str)
    (
        locked_logs,
        locked_users,
        locked_profiles,
        locked_memberships,
        locked_clans,
    ) = _lock_synthetic_reconciliation_rows(user_ids, reconciliation_clan_ids)
    career_pairs = sorted(
        _career_clan_map(catalog, locked_clans), key=lambda pair: pair[1].pk
    )
    career_clans = [clan for _item, clan in career_pairs]
    users_by_id = {user.pk: user for user in locked_users}
    if not users_by_id:
        has_legacy_logs = ActionLog.objects.filter(
            pk__in=[demo_id("action-log", str(index)) for index in range(24)]
        ).exists()
        if (
            any(clan_id in locked_clans for clan_id in legacy_clan_ids)
            or has_legacy_logs
        ):
            raise CommandError(
                "Legacy synthetic rows exist without their demo users; refusing reconciliation."
            )
        return False
    if len(users_by_id) != len(user_ids):
        raise CommandError(
            "The shared-dev demo user set is partial; refusing career reconciliation."
        )
    users = [users_by_id[identifier] for identifier in user_ids]
    if any(
        user.email != f"demo-{index:02d}@example.invalid"
        or user.role != User.Role.STUDENT
        or user.firebase_uid is not None
        or user.microsoft_oid is not None
        for index, user in enumerate(users, start=1)
    ):
        raise CommandError(
            "A deterministic demo user was edited or linked to an identity provider; refusing reconciliation."
        )
    profiles_by_id = {profile.user_id: profile for profile in locked_profiles}
    legacy_clans = {
        clan_id: locked_clans[clan_id]
        for clan_id in legacy_clan_ids
        if clan_id in locked_clans
    }
    demo_log_ids = [demo_id("action-log", str(index)) for index in range(24)]
    existing_demo_logs = ActionLog.objects.filter(pk__in=demo_log_ids).count()

    if len(profiles_by_id) != len(users):
        raise CommandError(
            "The shared-dev demo profile set is partial; refusing career reconciliation."
        )

    expected_new_clans: dict[Any, Clan] = {}
    is_v2 = True
    for index, user in enumerate(users):
        expected_clan = career_clans[index % len(career_clans)]
        profile = profiles_by_id[user.pk]
        if (
            profile.career != expected_clan.name
            or profile.institutional_clan_id != expected_clan.pk
        ):
            is_v2 = False
        expected_new_clans[user.pk] = expected_clan
    if is_v2:
        _validate_institutional_memberships(
            users, expected_new_clans, locked_memberships
        )
        _validate_retained_legacy_clans(legacy_clans, user_ids, demo_log_ids)
        private_clans = _existing_private_demo_clans()
        _validate_demo_logs(
            catalog, users, private_clans, expected_new_clans, locked_logs
        )
        return False

    if len(legacy_clans) != 3 or existing_demo_logs != 24:
        raise CommandError(
            "The shared-dev legacy seed is partial; refusing career reconciliation."
        )
    _validate_legacy_clan_identities(legacy_clans)
    old_clan_by_user: dict[Any, Clan] = {}
    for index, user in enumerate(users):
        old_clan = legacy_clans.get(legacy_clan_ids[index % 3])
        profile = profiles_by_id[user.pk]
        if (
            old_clan is None
            or profile.career != LEGACY_CAREER_LABELS[index % 3]
            or profile.institutional_clan_id != old_clan.pk
        ):
            raise CommandError(
                "A legacy demo profile or clan differs from the known synthetic seed; refusing reconciliation."
            )
        old_clan_by_user[user.pk] = old_clan

    old_memberships = _validate_institutional_memberships(
        users, old_clan_by_user, locked_memberships
    )
    private_clans = _existing_private_demo_clans()
    logs = _validate_demo_logs(
        catalog,
        users,
        private_clans,
        old_clan_by_user,
        locked_logs,
    )
    for user in users:
        expected_new = expected_new_clans[user.pk]
        if ClanMembership.objects.filter(user=user, clan=expected_new).exists():
            raise CommandError(
                "A demo user already has a career-clan membership outside the known legacy seed; refusing reconciliation."
            )

    deltas: dict[Any, int] = {}
    for log in logs:
        if log.status == ActionLog.Status.APPROVED:
            deltas[log.institutional_clan_id] = (
                deltas.get(log.institutional_clan_id, 0) - log.points_awarded
            )
            destination = expected_new_clans[log.user_id].pk
            deltas[destination] = deltas.get(destination, 0) + log.points_awarded

    affected_clan_ids = sorted(deltas, key=str)
    if any(clan_id not in locked_clans for clan_id in affected_clan_ids):
        raise CommandError(
            "A clan needed for demo reconciliation is missing; refusing writes."
        )
    for clan_id, delta in deltas.items():
        if locked_clans[clan_id].total_points + delta < 0:
            raise CommandError(
                "A legacy demo clan total cannot absorb the synthetic point delta; refusing reconciliation."
            )

    # Recheck all mutable clan identity fields on the locked rows directly
    # before the first profile, membership, or action-log reference is changed.
    _career_clan_map(catalog, locked_clans)
    _validate_legacy_clan_identities(legacy_clans)

    for user in users:
        profile = profiles_by_id[user.pk]
        destination = expected_new_clans[user.pk]
        profile.institutional_clan = destination
        profile.career = destination.name
        profile.save(update_fields=["institutional_clan", "career"])
        membership = old_memberships[user.pk]
        membership.clan = destination
        membership.save(update_fields=["clan"])
    for log in logs:
        log.institutional_clan = expected_new_clans[log.user_id]
        log.save(update_fields=["institutional_clan"])
    for clan_id, delta in deltas.items():
        if delta:
            Clan.all_objects.filter(pk=clan_id).update(
                total_points=F("total_points") + delta
            )
    return True


def _validate_institutional_memberships(
    users: list[Any],
    expected_clans: dict[Any, Clan],
    locked_memberships: list[ClanMembership],
) -> dict[Any, ClanMembership]:
    memberships = list(
        ClanMembership.objects.filter(
            pk__in=[membership.pk for membership in locked_memberships]
        )
        .select_related("clan")
        .order_by("user_id", "pk")
    )
    by_user = {membership.user_id: membership for membership in memberships}
    if len(memberships) != len(users) or len(by_user) != len(users):
        raise CommandError(
            "A demo user's institutional membership differs from the known seed; refusing reconciliation."
        )
    for user in users:
        membership = by_user[user.pk]
        if (
            membership.clan_id != expected_clans[user.pk].pk
            or membership.role != ClanMembership.MembershipRole.MEMBER
            or membership.status != ClanMembership.Status.ACCEPTED
            or membership.is_active_private
        ):
            raise CommandError(
                "A demo user's institutional membership was edited; refusing reconciliation."
            )
    return by_user


def _existing_private_demo_clans() -> list[Clan]:
    expected_ids = [demo_id("clan", "private-01"), demo_id("clan", "private-02")]
    by_id = {clan.pk: clan for clan in Clan.all_objects.filter(pk__in=expected_ids)}
    if len(by_id) != 2:
        raise CommandError(
            "A deterministic demo private clan differs from the known seed; refusing reconciliation."
        )
    clans = [by_id[identifier] for identifier in expected_ids]
    if any(
        clan.type != Clan.ClanType.PRIVATE
        or clan.privacy != Clan.Privacy.PRIVATE_INVITE
        or clan.deleted_at is not None
        for clan in clans
    ):
        raise CommandError(
            "A deterministic demo private clan differs from the known seed; refusing reconciliation."
        )
    return clans


def _validate_retained_legacy_clans(
    legacy_clans: dict[Any, Clan], user_ids: list[Any], demo_log_ids: list[Any]
) -> None:
    """Allow the old rows only as untouched, unreferenced synthetic history."""
    if not legacy_clans:
        return
    if len(legacy_clans) != 3:
        raise CommandError(
            "The retained legacy demo clan set is partial; refusing career reconciliation."
        )
    for index, clan_id in enumerate(
        [demo_id("clan", f"institutional-{number:02d}") for number in range(1, 4)]
    ):
        clan = legacy_clans.get(clan_id)
        if (
            clan is None
            or clan.name != LEGACY_INSTITUTIONAL_CLAN_NAMES[index]
            or clan.description != DEMO_INSTITUTIONAL_CLAN_DESCRIPTION
            or clan.type != Clan.ClanType.INSTITUTIONAL
            or clan.privacy != Clan.Privacy.PUBLIC
            or clan.deleted_at is not None
            or clan.created_by_id is not None
            or ClanMembership.objects.filter(user_id__in=user_ids, clan=clan).exists()
            or ActionLog.objects.filter(
                pk__in=demo_log_ids, institutional_clan=clan
            ).exists()
        ):
            raise CommandError(
                "A retained legacy demo clan was edited or still has demo references; refusing reconciliation."
            )


def load_dev_synthetic_catalog() -> CatalogData:
    """Require the fixed dev-only fixture pinned by its environment digest."""
    expected_digest = (
        os.environ.get(DEV_SYNTHETIC_CATALOG_SHA256_ENV, "").strip().lower()
    )
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise CommandError(
            f"{DEV_SYNTHETIC_CATALOG_SHA256_ENV} must contain the dev fixture SHA-256."
        )
    try:
        contents = DEV_SYNTHETIC_CATALOG.read_bytes()
    except OSError as error:
        raise CommandError(
            f"Could not read the fixed dev synthetic catalog {DEV_SYNTHETIC_CATALOG}: {error}"
        ) from error
    actual_digest = hashlib.sha256(contents).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise CommandError(
            "The dev synthetic catalog does not match its pinned SHA-256."
        )
    return load_catalog_content(
        contents, source=str(DEV_SYNTHETIC_CATALOG), expected_status="DRAFT"
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
    verified_tls = isinstance(options, dict) and (
        options.get("sslmode") == "verify-full"
        or (
            options.get("sslmode") == "require"
            and options.get("channel_binding") == "require"
        )
    )
    if not verified_tls:
        raise CommandError(
            "seed_neon_dev requires sslmode=verify-full or "
            "sslmode=require with channel_binding=require for Neon dev."
        )


def ensure_tls_connection() -> None:
    """Verify the actual database connection is encrypted before writes."""
    require_connection_tls(
        encrypted=True,
        inspection_error=(
            "Cannot verify the Neon dev TLS connection; refusing shared seed writes."
        ),
        mismatch_error="seed_neon_dev requires an encrypted PostgreSQL connection.",
    )


class Command(BaseCommand):
    """Seed pinned synthetic demo data into Neon dev, never other branches."""

    help = "Load synthetic demo data into the explicitly confirmed Neon dev database."

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
        catalog = load_dev_synthetic_catalog()
        as_of: datetime = parse_as_of(options.get("as_of"))
        ensure_tls_connection()

        with transaction.atomic():
            acquire_neon_dev_seed_lock()
            load_catalog_data(catalog)
            reconcile_legacy_demo_careers(catalog)
            result = seed_demo_data(catalog, as_of, shared_dev=True)

        return (
            f"Seeded Neon dev demo: {result.user_count} student users "
            f"({result.user_created} created), {result.private_clan_count} private clans, "
            f"{result.created_logs} action logs, {result.campaign_count} campaigns "
            f"({result.campaigns_created} created), {result.mission_count} missions."
        )
