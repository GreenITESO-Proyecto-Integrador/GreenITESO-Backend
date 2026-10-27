"""Load pinned synthetic demo data into only the shared Neon dev database."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from django.db.utils import IntegrityError, OperationalError
from django.utils import timezone

from green_iteso.accounts.management.commands.bootstrap_dev import (
    DEMO_INSTITUTIONAL_CLAN_DESCRIPTION,
    demo_action_log_identity,
    demo_id,
    expected_demo_action_status,
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


def load_dev_career_clans(catalog: CatalogData) -> list[Clan]:
    """Adopt onboarding rows without changing their identity, metadata or totals.

    Resolve in fixture-ID order so random onboarding UUIDs never reshuffle demo
    assignments. Prefer the live row; restore a sole deleted row only after all
    reconciliation checks. Multiple deleted candidates without a live row are
    ambiguous and require operator evidence.
    """
    result = []
    for item, fixture_id in sorted(
        _career_clan_specs(catalog), key=lambda pair: pair[1]
    ):
        pinned = Clan.all_objects.filter(pk=fixture_id).first()
        if pinned is not None and not _career_clan_matches_fixture(item, pinned):
            raise CommandError(
                "A synthetic career clan differs from the pinned fixture; refusing reconciliation."
            )
        candidates = list(Clan.all_objects.filter(name=item["name"]).order_by("pk"))
        live = [clan for clan in candidates if clan.deleted_at is None]
        if live:
            clan = live[0]
        elif len(candidates) == 1:
            clan = candidates[0]
        elif candidates:
            raise CommandError(
                "Ambiguous deleted career clans; refusing reconciliation."
            )
        else:
            clan, _created = Clan.objects.get_or_create(
                name=item["name"],
                defaults={
                    "id": fixture_id,
                    "description": f"{item['description']} Career key: {item['career']}",
                    "type": Clan.ClanType.INSTITUTIONAL,
                    "privacy": Clan.Privacy.PUBLIC,
                },
            )
        if not _career_clan_matches_fixture(item, clan):
            raise CommandError(
                "A synthetic career clan differs from the pinned fixture; refusing reconciliation."
            )
        result.append(clan)
    return result


def _career_clan_map(
    catalog: CatalogData,
    locked_clans: dict[Any, Clan],
    career_clans: list[Clan] | None = None,
) -> list[tuple[dict[str, Any], Clan]]:
    """Validate career destination identities from their already locked rows."""
    result: list[tuple[dict[str, Any], Clan]] = []
    snapshots = {clan.name: clan for clan in career_clans or []}
    for item, fixture_id in sorted(
        _career_clan_specs(catalog), key=lambda pair: pair[1]
    ):
        snapshot = snapshots.get(item["name"])
        clan_id = snapshot.pk if snapshot is not None else fixture_id
        clan = locked_clans.get(clan_id)
        if clan is None:
            raise CommandError(
                "A configured synthetic career clan is missing; refusing reconciliation."
            )
        if not _career_clan_matches_fixture(item, clan) or (
            snapshot is not None
            and (clan.deleted_at, clan.description)
            != (snapshot.deleted_at, snapshot.description)
        ):
            raise CommandError(
                "A synthetic career clan differs from the pinned fixture; refusing reconciliation."
            )
        result.append((item, clan))
    return result


def _career_clan_matches_fixture(item: dict[str, Any], clan: Clan) -> bool:
    """Require public institutional identity; preserve adopted-row metadata."""
    expected_description = f"{item['description']} Career key: {item['career']}"
    return (
        clan.name == item["name"]
        and (
            clan.pk != stable_reference_id("institutional-clan", item["key"])
            or clan.description == expected_description
        )
        and clan.type == Clan.ClanType.INSTITUTIONAL
        and clan.privacy == Clan.Privacy.PUBLIC
        and clan.created_by_id is None
    )


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
        if clan is None or not _legacy_clan_matches_fixture(index, clan):
            raise CommandError(
                "A legacy demo profile or clan differs from the known synthetic seed; refusing reconciliation."
            )


def _legacy_clan_matches_fixture(
    index: int, clan: Clan, *, allow_deleted: bool = False
) -> bool:
    """Compare every mutable source-clan identity field to the v1 fixture."""
    return (
        clan.name == LEGACY_INSTITUTIONAL_CLAN_NAMES[index]
        and clan.description == DEMO_INSTITUTIONAL_CLAN_DESCRIPTION
        and clan.type == Clan.ClanType.INSTITUTIONAL
        and clan.privacy == Clan.Privacy.PUBLIC
        and (allow_deleted or clan.deleted_at is None)
        and clan.created_by_id is None
    )


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
    actions = get_catalog_actions(catalog)
    for index in range(24):
        log = logs_by_id[demo_id("action-log", str(index))]
        user = users[index % len(users)]
        expected_action, expected_status = expected_demo_action_status(index, actions)
        expected_identity = (
            f"demo-action-{index:02d}",
            user.pk,
            expected_action.pk,
            expected_clans[user.pk].pk,
            private_clans[index % len(private_clans)].pk,
            expected_status,
            expected_action.points,
            expected_action.co2_kg_factor,
            expected_action.water_liters_factor,
            expected_action.plastic_kg_factor,
            demo_id("campaign", "active") if index < 12 else None,
            f"demo-only/action-{index:02d}.jpg"
            if expected_action.validation_type == ActionMaster.ValidationType.PHOTO
            else "",
            None,
            expected_status == ActionLog.Status.REJECTED,
            "Synthetic system-rejected example"
            if expected_status == ActionLog.Status.REJECTED
            else "",
        )
        if demo_action_log_identity(log) != expected_identity:
            raise CommandError(
                "A deterministic shared-dev action log was edited or has an unexpected "
                "identity; refusing reconciliation."
            )
        logs.append(log)
    return logs


# Keep the fail-closed checks and first-write boundary together so reviewers can
# audit the transaction's validation order as one sequence.
def reconcile_legacy_demo_careers(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    catalog: CatalogData,
    career_clans: list[Clan] | None = None,
) -> bool:
    """Move only a complete, verified v1 synthetic seed onto the four career clans.

    Returns True when the known v1 dataset was reconciled. A clean database and
    an already reconciled dataset return False. Partial or edited states fail
    closed. Only the 24 identity-verified synthetic logs may change attribution,
    including APPROVED ones; unrelated history and points are preserved.
    """
    user_ids = [demo_id("user", str(index)) for index in range(1, 21)]
    career_specs = _career_clan_specs(catalog)
    career_clan_ids = [clan_id for _item, clan_id in career_specs]
    legacy_clan_ids = [
        demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
    ]
    existing_career_ids = list(
        Clan.all_objects.filter(name__in=DEV_CAREER_LABELS).values_list("pk", flat=True)
    )
    reconciliation_clan_ids = sorted(
        set(career_clan_ids + legacy_clan_ids + existing_career_ids), key=str
    )
    (
        locked_logs,
        locked_users,
        locked_profiles,
        locked_memberships,
        locked_clans,
    ) = _lock_synthetic_reconciliation_rows(user_ids, reconciliation_clan_ids)
    career_pairs = _career_clan_map(catalog, locked_clans, career_clans)
    destination_clans = [clan for _item, clan in career_pairs]
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
        expected_clan = destination_clans[index % len(destination_clans)]
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

    old_clan_by_user: dict[Any, Clan] = {}
    previous_v2 = all(
        profiles_by_id[user.pk].career == expected_new_clans[user.pk].name
        for user in users
    )
    if previous_v2:
        _validate_retained_legacy_clans(legacy_clans, user_ids, demo_log_ids)
        items_by_name = {item["name"]: item for item, _clan in career_pairs}
        for user in users:
            profile = profiles_by_id[user.pk]
            old_clan = locked_clans.get(profile.institutional_clan_id)
            destination = expected_new_clans[user.pk]
            if (
                old_clan is None
                or not _career_clan_matches_fixture(
                    items_by_name[destination.name], old_clan
                )
                or (old_clan.pk != destination.pk and old_clan.deleted_at is None)
            ):
                raise CommandError(
                    "A previous synthetic career clan differs from the known seed; refusing reconciliation."
                )
            old_clan_by_user[user.pk] = old_clan
    else:
        if len(legacy_clans) != 3 or existing_demo_logs != 24:
            raise CommandError(
                "The shared-dev legacy seed is partial; refusing career reconciliation."
            )
        _validate_legacy_clan_identities(legacy_clans)
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
        if (
            expected_new.pk != old_clan_by_user[user.pk].pk
            and ClanMembership.objects.filter(user=user, clan=expected_new).exists()
        ):
            raise CommandError(
                "A demo user already has a career-clan membership outside the known "
                "legacy seed; refusing reconciliation."
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
    _career_clan_map(catalog, locked_clans, career_clans)
    if not previous_v2:
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
        has_demo_references = clan is not None and (
            ClanMembership.objects.filter(user_id__in=user_ids, clan=clan).exists()
            or ActionLog.objects.filter(
                pk__in=demo_log_ids, institutional_clan=clan
            ).exists()
        )
        if (
            clan is None
            or not _legacy_clan_matches_fixture(index, clan, allow_deleted=True)
            or has_demo_references
        ):
            raise CommandError(
                "A retained legacy demo clan was edited or still has demo references; refusing reconciliation."
            )


def finish_career_clans(career_clans: list[Clan]) -> None:
    """Restore chosen careers and retire only unreferenced synthetic placeholders.

    Full NOWAIT locks prevent FK inserts racing the emptiness check; contention
    aborts the transaction for retry instead of waiting in a different lock order.
    """
    for clan in career_clans:
        if clan.deleted_at is not None:
            Clan.all_objects.filter(pk=clan.pk).update(deleted_at=None)
    legacy_ids = [
        demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
    ]
    try:
        legacy_clans = list(
            Clan.all_objects.select_for_update(nowait=True)
            .filter(pk__in=legacy_ids)
            .order_by("pk")
        )
    except OperationalError as error:
        if getattr(error.__cause__, "sqlstate", None) == "55P03":
            raise CommandError(
                "A concurrent write is holding a legacy clan; retry the Neon dev seed."
            ) from error
        raise
    for clan in legacy_clans:
        if clan.deleted_at is None and not (
            UserProfile.objects.filter(institutional_clan=clan).exists()
            or ClanMembership.objects.filter(clan=clan).exists()
            or ActionLog.objects.filter(institutional_clan=clan).exists()
        ):
            clan.deleted_at = timezone.now()
            clan.save(update_fields=["deleted_at"])


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
            load_catalog_data(replace(catalog, clans=()))
            career_clans = load_dev_career_clans(catalog)
            reconcile_legacy_demo_careers(catalog, career_clans)
            try:
                finish_career_clans(career_clans)
            except IntegrityError as error:
                raise CommandError(
                    "Concurrent career name collision; retry the Neon dev seed."
                ) from error
            result = seed_demo_data(
                catalog, as_of, shared_dev=True, career_clans=career_clans
            )

        return (
            f"Seeded Neon dev demo: {result.user_count} student users "
            f"({result.user_created} created), {result.private_clan_count} private clans, "
            f"{result.created_logs} action logs, {result.campaign_count} campaigns "
            f"({result.campaigns_created} created), {result.mission_count} missions."
        )
