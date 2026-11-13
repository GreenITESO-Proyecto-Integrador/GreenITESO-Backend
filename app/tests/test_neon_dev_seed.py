"""Safety checks for the separately gated shared Neon-dev demo seed."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event, get_ident
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections, transaction

from green_iteso.accounts.management.commands import seed_neon_dev
from green_iteso.accounts.management.commands.bootstrap_dev import (
    create_demo_users,
    demo_id,
    refresh_clan_totals,
    seed_demo_data,
)
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands.load_catalog import (
    DEFAULT_CATALOG,
    load_catalog_data,
    stable_reference_id,
    validate_catalog,
)
from green_iteso.actions.models import (
    ActionCategory,
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.clans.services import (
    assign_institutional_clan,
    assign_leader,
    transfer_leadership,
)
from green_iteso.settings.neon_endpoints import CANONICAL_NEON_ENDPOINTS

DEV_CAREERS = (
    "Ingeniería en Desarrollo de Software",
    "Ingeniería en Sistemas Computacionales",
    "Ingeniería en Mecatrónica",
    "Ingeniería en Ciberseguridad",
)


def approved_payload() -> dict[str, Any]:
    """Create test-only approved-shaped data; this is not Product ratification."""
    payload = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    payload["status"] = "APPROVED"
    payload["approval"] = {
        "approved_by": "Product (test fixture only)",
        "reference": "TEST-ONLY",
        "approved_at": "2030-01-01T00:00:00Z",
    }
    return payload


def pin_catalog_digest(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        seed_neon_dev.DEV_SYNTHETIC_CATALOG_SHA256_ENV,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_rejects_approved_catalog_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(approved_payload()), encoding="utf-8")
    monkeypatch.setattr(seed_neon_dev, "DEV_SYNTHETIC_CATALOG", path)
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(
        connection,
        "cursor",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )

    with pytest.raises(CommandError, match="DRAFT"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)


def test_neon_dev_seed_requires_explicit_target_confirmation() -> None:
    with pytest.raises(CommandError, match="--confirm-target dev"):
        call_command("seed_neon_dev", confirm_target="", verbosity=0)


def test_neon_dev_seed_requires_independently_pinned_synthetic_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    monkeypatch.delenv(seed_neon_dev.DEV_SYNTHETIC_CATALOG_SHA256_ENV, raising=False)
    with pytest.raises(CommandError, match="SHA-256"):
        seed_neon_dev.load_dev_synthetic_catalog()
    monkeypatch.setenv(seed_neon_dev.DEV_SYNTHETIC_CATALOG_SHA256_ENV, "0" * 64)
    with pytest.raises(CommandError, match="does not match"):
        seed_neon_dev.load_dev_synthetic_catalog()
    pin_catalog_digest(path, monkeypatch)
    catalog = seed_neon_dev.load_dev_synthetic_catalog()
    assert len(catalog.categories) == 4
    assert len(catalog.actions) == 6
    assert tuple(clan["career"] for clan in catalog.clans) == DEV_CAREERS


def test_neon_dev_seed_does_not_accept_an_arbitrary_catalog_path() -> None:
    with pytest.raises(TypeError, match="Unknown option.*input"):
        call_command(
            "seed_neon_dev",
            input=Path("/tmp/unapproved-catalog.json"),
            confirm_target="dev",
            verbosity=0,
        )


@pytest.mark.parametrize(
    ("environment", "deployed", "role", "host", "user"),
    [
        (
            "staging",
            True,
            "app",
            CANONICAL_NEON_ENDPOINTS["staging"]["pooled"],
            "greeniteso_dev_app",
        ),
        (
            "production",
            True,
            "app",
            CANONICAL_NEON_ENDPOINTS["production"]["pooled"],
            "greeniteso_dev_app",
        ),
        (
            "dev",
            False,
            "app",
            CANONICAL_NEON_ENDPOINTS["dev"]["pooled"],
            "greeniteso_dev_app",
        ),
        (
            "dev",
            True,
            "direct",
            CANONICAL_NEON_ENDPOINTS["dev"]["direct"],
            "greeniteso_dev_migrator",
        ),
        ("dev", True, "app", "localhost", "greeniteso_dev_app"),
        ("dev", True, "app", CANONICAL_NEON_ENDPOINTS["dev"]["pooled"], "neondb_owner"),
    ],
)
def test_neon_dev_target_guard_rejects_other_targets_and_roles(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    deployed: bool,
    role: str,
    host: str,
    user: str,
) -> None:
    from green_iteso.accounts.management.commands.seed_neon_dev import (
        ensure_neon_dev_target,
    )

    monkeypatch.setenv("DJANGO_ENV", environment)
    monkeypatch.setattr(settings, "DEPLOYED", deployed)
    monkeypatch.setattr(settings, "CONNECTION_ROLE", role)
    monkeypatch.setitem(settings.DATABASES["default"], "HOST", host)
    monkeypatch.setitem(settings.DATABASES["default"], "USER", user)

    with pytest.raises(CommandError):
        ensure_neon_dev_target()


def test_neon_dev_target_guard_accepts_only_canonical_pooled_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from green_iteso.accounts.management.commands.seed_neon_dev import (
        ensure_neon_dev_target,
    )

    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(settings, "CONNECTION_ROLE", "app")
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "HOST",
        CANONICAL_NEON_ENDPOINTS["dev"]["pooled"],
    )
    monkeypatch.setitem(settings.DATABASES["default"], "USER", "greeniteso_dev_app")
    for options in (
        {"sslmode": "verify-full"},
        {"sslmode": "require", "channel_binding": "require"},
    ):
        monkeypatch.setitem(settings.DATABASES["default"], "OPTIONS", options)
        ensure_neon_dev_target()

    monkeypatch.setitem(
        settings.DATABASES["default"], "OPTIONS", {"sslmode": "require"}
    )
    with pytest.raises(CommandError, match="channel_binding=require"):
        ensure_neon_dev_target()


def test_approved_catalog_requires_explicit_approval_metadata() -> None:
    catalog = validate_catalog(approved_payload(), expected_status="APPROVED")
    assert len(catalog.actions) == 3

    missing_metadata = approved_payload()
    missing_metadata.pop("approval")
    with pytest.raises(CommandError, match="approval"):
        validate_catalog(missing_metadata, expected_status="APPROVED")


def test_local_catalog_importer_rejects_approved_payload_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(approved_payload()), encoding="utf-8")
    monkeypatch.setattr(
        connection,
        "cursor",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )

    with pytest.raises(CommandError, match="Only status=DRAFT"):
        call_command("load_catalog", input=path, verbosity=0)


def test_neon_dev_seed_rejects_unencrypted_runtime_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from green_iteso.accounts.management.commands.seed_neon_dev import (
        ensure_tls_connection,
    )

    raw_connection = SimpleNamespace(pgconn=SimpleNamespace(ssl_in_use=False))
    monkeypatch.setattr(connection, "ensure_connection", lambda: None)
    monkeypatch.setattr(connection, "connection", raw_connection)

    with pytest.raises(CommandError, match="encrypted PostgreSQL connection"):
        ensure_tls_connection()


@pytest.mark.django_db(transaction=True)
def test_shared_dev_demo_seed_is_idempotent_and_uses_student_accounts() -> None:
    payload = approved_payload()
    payload["institutional_clans"].sort(
        key=lambda clan: str(stable_reference_id("institutional-clan", clan["key"])),
        reverse=True,
    )
    catalog = validate_catalog(payload, expected_status="APPROVED")
    as_of = datetime.fromisoformat("2030-01-15T12:00:00+00:00")

    with transaction.atomic():
        load_catalog_data(catalog)
        first = seed_demo_data(catalog, as_of, shared_dev=True)
    first_counts = (User.objects.count(),)

    with transaction.atomic():
        second = seed_demo_data(catalog, as_of, shared_dev=True)

    assert first.user_created == 20
    assert second.user_created == 0
    assert User.objects.count() == first_counts[0] == 20
    assert set(User.objects.values_list("role", flat=True)) == {User.Role.STUDENT}
    assert not ActionLog.objects.filter(
        status=ActionLog.Status.REJECTED, reviewed_by__isnull=False
    ).exists()
    assert not ActionLog.objects.filter(
        status=ActionLog.Status.REJECTED, reviewed_at__isnull=True
    ).exists()
    assert (
        not ActionLog.objects.filter(status=ActionLog.Status.REJECTED)
        .exclude(rejection_reason="Synthetic system-rejected example")
        .exists()
    )
    expected_careers = {
        stable_reference_id("institutional-clan", clan["key"]): clan["career"]
        for clan in payload["institutional_clans"]
    }
    assert all(
        expected_careers[profile.institutional_clan_id] == profile.career
        for profile in UserProfile.objects.all()
    )
    for clan in Clan.objects.all():
        logs = ActionLog.objects.filter(status=ActionLog.Status.APPROVED)
        if clan.type == Clan.ClanType.INSTITUTIONAL:
            logs = logs.filter(institutional_clan=clan)
        else:
            logs = logs.filter(credited_private_clan=clan)
        assert clan.total_points == sum(logs.values_list("points_awarded", flat=True))
    for profile in UserProfile.objects.select_related("user"):
        expected_points = sum(
            ActionLog.objects.filter(
                user=profile.user, status=ActionLog.Status.APPROVED
            ).values_list("points_awarded", flat=True)
        )
        assert profile.total_points == expected_points
        assert profile.available_points == expected_points

    profile = UserProfile.objects.order_by("user_id").first()
    assert profile is not None
    initial_available = profile.available_points
    if initial_available:
        profile.available_points -= 1
        profile.save(update_fields=["available_points"])
        with transaction.atomic():
            seed_demo_data(catalog, as_of, shared_dev=True)
        profile.refresh_from_db()
        assert profile.available_points == initial_available - 1


@pytest.mark.django_db(transaction=True)
def test_shared_dev_clan_total_uses_atomic_increment() -> None:
    clan = Clan.objects.create(
        name="Existing shared-dev points",
        type=Clan.ClanType.INSTITUTIONAL,
        total_points=11,
    )

    refresh_clan_totals([], [clan], {clan.pk: 7}, shared_dev=True)

    clan.refresh_from_db()
    assert clan.total_points == 18


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("identity_field", ["firebase_uid", "microsoft_oid"])
def test_shared_dev_seed_refuses_identity_provider_linked_demo_user(
    identity_field: str,
) -> None:
    users, _ = create_demo_users(shared_dev=True)
    user = users[0]
    setattr(
        user,
        identity_field,
        "real-provider-identity"
        if identity_field == "firebase_uid"
        else "650e8400-e29b-41d4-a716-446655440000",
    )
    user.save(update_fields=[identity_field])

    with pytest.raises(CommandError, match="identity provider"):
        create_demo_users(shared_dev=True)


@pytest.mark.django_db(transaction=True)
def test_shared_dev_seed_rejects_existing_demo_log_with_wrong_clan() -> None:
    catalog = validate_catalog(approved_payload(), expected_status="APPROVED")
    as_of = datetime.fromisoformat("2030-01-15T12:00:00+00:00")
    with transaction.atomic():
        load_catalog_data(catalog)
        seed_demo_data(catalog, as_of, shared_dev=True)

    log = ActionLog.objects.order_by("pk").first()
    assert log is not None
    other_clan = (
        Clan.objects.filter(type=Clan.ClanType.PRIVATE)
        .exclude(pk=log.credited_private_clan_id)
        .first()
    )
    assert other_clan is not None
    log.credited_private_clan = other_clan
    log.save(update_fields=["credited_private_clan"])

    with pytest.raises(CommandError, match="identity collision"):
        with transaction.atomic():
            seed_demo_data(catalog, as_of, shared_dev=True)


@pytest.mark.django_db(transaction=True)
def test_neon_dev_command_is_idempotent_and_loads_its_fixed_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    call_command("seed_neon_dev", **options, verbosity=0)
    first_counts = (User.objects.count(), ActionLog.objects.count())
    call_command("seed_neon_dev", **options, verbosity=0)

    assert first_counts == (20, 24)
    assert (User.objects.count(), ActionLog.objects.count()) == first_counts
    assert ActionCategory.objects.count() == 4
    assert ActionMaster.objects.count() == 6
    assert set(UserProfile.objects.values_list("career", flat=True)) == set(DEV_CAREERS)
    assert set(
        Clan.objects.filter(type=Clan.ClanType.INSTITUTIONAL).values_list(
            "name", flat=True
        )
    ) == set(DEV_CAREERS)
    assert all(
        profile.career == profile.institutional_clan.name
        for profile in UserProfile.objects.select_related("institutional_clan")
    )
    assert set(User.objects.values_list("role", flat=True)) == {User.Role.STUDENT}
    assert not ActionLogMissionContribution.objects.exclude(
        action_log__status=ActionLog.Status.APPROVED
    ).exists()


def _seed_legacy_neon_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build the complete pre-career synthetic dataset for upgrade tests."""
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    catalog = replace(seed_neon_dev.load_dev_synthetic_catalog(), clans=())
    as_of = datetime.fromisoformat("2030-01-15T12:00:00+00:00")
    with transaction.atomic():
        load_catalog_data(catalog)
        seed_demo_data(catalog, as_of, shared_dev=True)


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_reconciles_complete_legacy_demo_and_reruns_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    unrelated_user = User.objects.create_user("peer-test@example.invalid")
    unrelated_clan = Clan.all_objects.get(pk=demo_id("clan", "institutional-01"))
    unrelated_action = ActionMaster.objects.order_by("code").first()
    assert unrelated_action is not None
    unrelated_log = ActionLog.objects.create(
        user=unrelated_user,
        action=unrelated_action,
        institutional_clan=unrelated_clan,
        idempotency_key="peer-test-action",
        points_awarded=7,
        status=ActionLog.Status.APPROVED,
    )
    unrelated_clan.total_points += 7
    unrelated_clan.save(update_fields=["total_points"])
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    call_command("seed_neon_dev", **options, verbosity=0)

    profiles = list(UserProfile.objects.select_related("institutional_clan"))
    assert len(profiles) == 20
    assert {profile.career for profile in profiles} == set(DEV_CAREERS)
    assert all(
        profile.career == profile.institutional_clan.name for profile in profiles
    )
    for user_id, clan_id in UserProfile.objects.values_list(
        "user_id", "institutional_clan_id"
    ):
        assert ClanMembership.objects.filter(
            user_id=user_id,
            clan_id=clan_id,
            role=ClanMembership.MembershipRole.MEMBER,
            status=ClanMembership.Status.ACCEPTED,
            is_active_private=False,
        ).exists()
    assert not ClanMembership.objects.filter(
        user_id__in=UserProfile.objects.values("user_id"),
        clan_id__in=[
            demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
        ],
    ).exists()
    assert not ActionLog.objects.filter(
        pk__in=[demo_id("action-log", str(index)) for index in range(24)],
        institutional_clan_id__in=[
            demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)
        ],
    ).exists()
    unrelated_log.refresh_from_db()
    unrelated_clan.refresh_from_db()
    assert unrelated_log.institutional_clan_id == demo_id("clan", "institutional-01")
    assert unrelated_clan.total_points == 7

    for clan in Clan.objects.all():
        logs = ActionLog.objects.filter(
            institutional_clan=clan, status=ActionLog.Status.APPROVED
        )
        if clan.type == Clan.ClanType.PRIVATE:
            logs = ActionLog.objects.filter(
                credited_private_clan=clan, status=ActionLog.Status.APPROVED
            )
        assert clan.total_points == sum(logs.values_list("points_awarded", flat=True))

    before = (
        tuple(
            UserProfile.objects.order_by("user_id").values_list(
                "career", "institutional_clan_id"
            )
        ),
        tuple(Clan.objects.order_by("pk").values_list("pk", "total_points")),
        tuple(
            ActionLog.objects.order_by("pk").values_list("pk", "institutional_clan_id")
        ),
        ClanMembership.objects.count(),
    )
    call_command("seed_neon_dev", **options, verbosity=0)
    after = (
        tuple(
            UserProfile.objects.order_by("user_id").values_list(
                "career", "institutional_clan_id"
            )
        ),
        tuple(Clan.objects.order_by("pk").values_list("pk", "total_points")),
        tuple(
            ActionLog.objects.order_by("pk").values_list("pk", "institutional_clan_id")
        ),
        ClanMembership.objects.count(),
    )
    assert after == before


@pytest.mark.django_db(transaction=True)
def test_concurrent_neon_dev_seed_runs_reconcile_legacy_rows_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}
    barrier = Barrier(2)

    def run_seed() -> str:
        barrier.wait(timeout=10)
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_seed)
        second = executor.submit(run_seed)
        first.result(timeout=30)
        second.result(timeout=30)

    assert User.objects.count() == 20
    assert ActionLog.objects.count() == 24
    assert set(UserProfile.objects.values_list("career", flat=True)) == set(DEV_CAREERS)
    for clan in Clan.objects.filter(name__in=DEV_CAREERS):
        approved_points = ActionLog.objects.filter(
            institutional_clan=clan, status=ActionLog.Status.APPROVED
        ).values_list("points_awarded", flat=True)
        assert clan.total_points == sum(approved_points)


@pytest.mark.django_db(transaction=True)
def test_audit_status_update_serializes_before_career_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    pending_log = ActionLog.objects.get(pk=demo_id("action-log", "1"))
    pending_log_id = pending_log.pk
    audit_started = Event()
    release_audit = Event()
    seed_reached_row_locks = Event()
    seed_finished = Event()
    original_lock_rows = seed_neon_dev._lock_synthetic_reconciliation_rows

    def signal_before_row_locks(user_ids: list[Any], clan_ids: list[Any]) -> Any:
        seed_reached_row_locks.set()
        return original_lock_rows(user_ids, clan_ids)

    monkeypatch.setattr(
        seed_neon_dev,
        "_lock_synthetic_reconciliation_rows",
        signal_before_row_locks,
    )
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def audit_update() -> None:
        try:
            with transaction.atomic():
                log = ActionLog.objects.select_for_update().get(pk=pending_log_id)
                old_clan_id = log.institutional_clan_id
                profile = UserProfile.objects.select_for_update().get(
                    user_id=log.user_id
                )
                clan = Clan.all_objects.select_for_update().get(pk=old_clan_id)
                audit_started.set()
                if not release_audit.wait(timeout=10):
                    raise TimeoutError("Test did not release simulated audit update")
                log.status = ActionLog.Status.APPROVED
                log.reviewed_at = datetime.fromisoformat("2030-01-16T12:00:00+00:00")
                log.reviewed_by_id = User.objects.order_by("pk").first().pk
                log.save(update_fields=["status", "reviewed_at", "reviewed_by"])
                profile.total_points += log.points_awarded
                profile.available_points += log.points_awarded
                profile.save(update_fields=["total_points", "available_points"])
                clan.total_points += log.points_awarded
                clan.save(update_fields=["total_points"])
        finally:
            connections.close_all()

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            seed_finished.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        audit = executor.submit(audit_update)
        assert audit_started.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_row_locks.wait(timeout=10)
        # The seed must wait for the existing audit row lock, then validate the
        # committed status instead of calculating from a stale pending snapshot.
        assert not seed_finished.wait(timeout=0.2)
        release_audit.set()
        audit.result(timeout=20)
        with pytest.raises(CommandError, match="edited or has an unexpected identity"):
            seed.result(timeout=20)

    pending_log.refresh_from_db()
    assert pending_log.status == ActionLog.Status.APPROVED
    assert pending_log.institutional_clan_id == demo_id("clan", "institutional-02")
    profile = UserProfile.objects.get(user_id=pending_log.user_id)
    assert profile.career.startswith("DEMO-CAREER-")


@pytest.mark.django_db(transaction=True)
def test_onboarding_membership_insert_completes_while_seed_waits_for_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    user = User.objects.get(pk=demo_id("user", "1"))
    membership_insert_started = Event()
    release_membership_insert = Event()
    seed_reached_profile_locks = Event()
    seed_backend_pids: list[int] = []
    original_get_or_create = ClanMembership.objects.get_or_create
    original_lock_profiles = seed_neon_dev._lock_synthetic_profiles

    def pause_before_membership_insert(*args: Any, **kwargs: Any) -> Any:
        membership_insert_started.set()
        if not release_membership_insert.wait(timeout=10):
            raise TimeoutError("Test did not release onboarding membership insert")
        return original_get_or_create(*args, **kwargs)

    def signal_before_profile_locks(user_ids: list[Any]) -> list[UserProfile]:
        seed_backend_pids.append(connection.connection.info.backend_pid)
        seed_reached_profile_locks.set()
        return original_lock_profiles(user_ids)

    monkeypatch.setattr(
        ClanMembership.objects, "get_or_create", pause_before_membership_insert
    )
    monkeypatch.setattr(
        seed_neon_dev, "_lock_synthetic_profiles", signal_before_profile_locks
    )
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def assign_career() -> UserProfile:
        try:
            return assign_institutional_clan(
                user=User.objects.get(pk=user.pk), career="Concurrent test assignment"
            )
        finally:
            connections.close_all()

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assignment = executor.submit(assign_career)
        assert membership_insert_started.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_profile_locks.wait(timeout=10)
        # At this point the seed holds its user lock and is about to wait on the
        # profile row. The FK check for onboarding's membership insert must be
        # compatible with that user lock, so onboarding can commit and release
        # the profile for the seed to validate.
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [seed_backend_pids[0]],
                )
                activity = cursor.fetchone()
            if activity is not None and activity[0] == "Lock" and activity[2]:
                break
            sleep(0.01)
        else:
            pytest.fail(
                "Seed did not wait on the profile row held by onboarding; "
                f"last activity was {activity!r}"
            )
        release_membership_insert.set()
        assigned_profile = assignment.result(timeout=20)
        assert assigned_profile.career == "Concurrent test assignment"
        with pytest.raises(CommandError, match="legacy demo profile or clan differs"):
            seed.result(timeout=20)

    assigned_profile.refresh_from_db()
    assert assigned_profile.career == "Concurrent test assignment"


@pytest.mark.django_db(transaction=True)
def test_destination_clan_is_revalidated_after_waiting_for_its_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    catalog = seed_neon_dev.load_dev_synthetic_catalog()
    with transaction.atomic():
        load_catalog_data(catalog)

    target_clan_id = stable_reference_id("institutional-clan", catalog.clans[-1]["key"])
    editor_has_lock = Event()
    release_editor = Event()
    seed_reached_clan_locks = Event()
    seed_backend_pids: list[int] = []
    original_lock_clans = seed_neon_dev._lock_synthetic_clans

    def edit_destination_clan() -> None:
        try:
            with transaction.atomic():
                clan = Clan.all_objects.select_for_update().get(pk=target_clan_id)
                editor_has_lock.set()
                if not release_editor.wait(timeout=10):
                    raise TimeoutError("Test did not release destination clan editor")
                clan.deleted_at = datetime.fromisoformat("2030-01-16T12:00:00+00:00")
                clan.save(update_fields=["deleted_at"])
        finally:
            connections.close_all()

    def signal_before_clan_locks(clan_ids: list[Any]) -> dict[Any, Clan]:
        expected_ids = {
            stable_reference_id("institutional-clan", item["key"])
            for item in catalog.clans
        } | {demo_id("clan", f"institutional-{index:02d}") for index in range(1, 4)}
        assert set(clan_ids) == expected_ids
        seed_backend_pids.append(connection.connection.info.backend_pid)
        seed_reached_clan_locks.set()
        return original_lock_clans(clan_ids)

    monkeypatch.setattr(
        seed_neon_dev, "_lock_synthetic_clans", signal_before_clan_locks
    )
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        editor = executor.submit(edit_destination_clan)
        assert editor_has_lock.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_clan_locks.wait(timeout=10)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [seed_backend_pids[0]],
                )
                activity = cursor.fetchone()
            if activity is not None and activity[0] == "Lock" and activity[2]:
                break
            sleep(0.01)
        else:
            release_editor.set()
            pytest.fail(
                "Seed did not wait for the destination clan lock; "
                f"last activity was {activity!r}"
            )
        release_editor.set()
        editor.result(timeout=20)
        with pytest.raises(CommandError, match="synthetic career clan differs"):
            seed.result(timeout=20)

    target_clan = Clan.all_objects.get(pk=target_clan_id)
    assert target_clan.deleted_at is not None
    assert UserProfile.objects.filter(institutional_clan_id=target_clan_id).count() == 0


@pytest.mark.django_db(transaction=True)
def test_leader_assignment_completes_before_seed_locks_memberships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    membership = (
        ClanMembership.objects.filter(clan__type=Clan.ClanType.INSTITUTIONAL)
        .order_by("user_id", "pk")
        .first()
    )
    assert membership is not None
    membership_id = membership.pk
    clan = Clan.objects.get(pk=membership.clan_id)
    leader_has_clan_lock = Event()
    release_leader = Event()
    seed_reached_clan_locks = Event()
    seed_backend_pids: list[int] = []
    original_membership_save = ClanMembership.save
    original_lock_clans = seed_neon_dev._lock_synthetic_clans

    def pause_leader_membership_write(
        self: ClanMembership, *args: Any, **kwargs: Any
    ) -> None:
        if (
            self.pk == membership_id
            and self.role == ClanMembership.MembershipRole.LEADER
        ):
            leader_has_clan_lock.set()
            if not release_leader.wait(timeout=10):
                raise TimeoutError("Test did not release leader assignment")
        original_membership_save(self, *args, **kwargs)

    def signal_before_clan_locks(clan_ids: list[Any]) -> dict[Any, Clan]:
        seed_backend_pids.append(connection.connection.info.backend_pid)
        seed_reached_clan_locks.set()
        return original_lock_clans(clan_ids)

    monkeypatch.setattr(ClanMembership, "save", pause_leader_membership_write)
    monkeypatch.setattr(
        seed_neon_dev, "_lock_synthetic_clans", signal_before_clan_locks
    )
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def promote_membership() -> ClanMembership:
        try:
            return assign_leader(
                clan=Clan.objects.get(pk=clan.pk),
                membership=ClanMembership.objects.get(pk=membership_id),
            )
        finally:
            connections.close_all()

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(promote_membership)
        assert leader_has_clan_lock.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_clan_locks.wait(timeout=10)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [seed_backend_pids[0]],
                )
                activity = cursor.fetchone()
            if activity is not None and activity[0] == "Lock" and activity[2]:
                break
            sleep(0.01)
        else:
            release_leader.set()
            pytest.fail(
                "Seed did not wait for the clan row held by assign_leader; "
                f"last activity was {activity!r}"
            )
        release_leader.set()
        promoted = leader.result(timeout=20)
        assert promoted.role == ClanMembership.MembershipRole.LEADER
        with pytest.raises(CommandError, match="institutional membership was edited"):
            seed.result(timeout=20)

    profile = UserProfile.objects.get(user_id=membership.user_id)
    assert profile.career.startswith("DEMO-CAREER-")


@pytest.mark.django_db(transaction=True)
def test_transfer_leadership_completes_before_seed_locks_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    clan = Clan.objects.get(pk=demo_id("clan", "institutional-01"))
    memberships = list(
        ClanMembership.objects.filter(clan=clan).order_by("user_id", "pk")[:2]
    )
    assert len(memberships) == 2
    actor_membership, successor_membership = memberships
    assign_leader(clan=clan, membership=actor_membership)

    transfer_has_clan_lock = Event()
    release_transfer = Event()
    seed_reached_clan_locks = Event()
    seed_backend_pids: list[int] = []
    transfer_thread_ids: list[int] = []
    original_user_lock = User.objects.select_for_update
    original_clan_locks = seed_neon_dev._lock_synthetic_clans

    def pause_transfer_user_lock(*args: Any, **kwargs: Any) -> Any:
        if get_ident() in transfer_thread_ids:
            transfer_has_clan_lock.set()
            if not release_transfer.wait(timeout=10):
                raise TimeoutError("Test did not release leadership transfer")
        return original_user_lock(*args, **kwargs)

    def signal_before_clan_locks(clan_ids: list[Any]) -> dict[Any, Clan]:
        seed_backend_pids.append(connection.connection.info.backend_pid)
        seed_reached_clan_locks.set()
        return original_clan_locks(clan_ids)

    monkeypatch.setattr(User.objects, "select_for_update", pause_transfer_user_lock)
    monkeypatch.setattr(
        seed_neon_dev, "_lock_synthetic_clans", signal_before_clan_locks
    )
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def run_transfer() -> ClanMembership:
        transfer_thread_ids.append(get_ident())
        try:
            return transfer_leadership(
                clan=Clan.objects.get(pk=clan.pk),
                actor=User.objects.get(pk=actor_membership.user_id),
                successor=User.objects.get(pk=successor_membership.user_id),
            )
        finally:
            connections.close_all()

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        transfer = executor.submit(run_transfer)
        assert transfer_has_clan_lock.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_clan_locks.wait(timeout=10)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [seed_backend_pids[0]],
                )
                activity = cursor.fetchone()
            if activity is not None and activity[0] == "Lock" and activity[2]:
                break
            sleep(0.01)
        else:
            release_transfer.set()
            pytest.fail(
                "Seed did not wait on the clan row held by leadership transfer; "
                f"last activity was {activity!r}"
            )
        release_transfer.set()
        promoted = transfer.result(timeout=20)
        assert promoted.role == ClanMembership.MembershipRole.LEADER
        with pytest.raises(CommandError, match="institutional membership was edited"):
            seed.result(timeout=20)

    assert UserProfile.objects.get(user_id=actor_membership.user_id).career.startswith(
        "DEMO-CAREER-"
    )


@pytest.mark.django_db(transaction=True)
def test_in_flight_membership_insert_makes_seed_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    catalog = seed_neon_dev.load_dev_synthetic_catalog()
    with transaction.atomic():
        load_catalog_data(catalog)
    user_id = demo_id("user", "1")
    destination_id = stable_reference_id("institutional-clan", catalog.clans[0]["key"])
    membership_inserted = Event()
    release_insert = Event()
    seed_reached_user_locks = Event()
    original_user_lock = seed_neon_dev._lock_synthetic_users

    def insert_membership() -> None:
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # Django's PostgreSQL FK constraints can be deferred. Force
                    # the INSERT's FK check now so its KEY SHARE is held while
                    # seed reconciliation reaches the strong User row lock.
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                ClanMembership.objects.create(
                    user=User.objects.get(pk=user_id),
                    clan=Clan.all_objects.get(pk=destination_id),
                    role=ClanMembership.MembershipRole.MEMBER,
                    status=ClanMembership.Status.ACCEPTED,
                )
                membership_inserted.set()
                if not release_insert.wait(timeout=10):
                    raise TimeoutError(
                        "Test did not release concurrent membership insert"
                    )
        finally:
            connections.close_all()

    def signal_before_user_locks(user_ids: list[Any]) -> list[User]:
        seed_reached_user_locks.set()
        return original_user_lock(user_ids)

    monkeypatch.setattr(
        seed_neon_dev, "_lock_synthetic_users", signal_before_user_locks
    )
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    def run_seed() -> None:
        try:
            return call_command("seed_neon_dev", **options, verbosity=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        insert = executor.submit(insert_membership)
        assert membership_inserted.wait(timeout=10)
        seed = executor.submit(run_seed)
        assert seed_reached_user_locks.wait(timeout=10)
        with pytest.raises(CommandError, match="concurrent write is holding"):
            seed.result(timeout=10)
        release_insert.set()
        insert.result(timeout=20)

    assert ClanMembership.objects.filter(
        user_id=user_id,
        clan_id=destination_id,
        role=ClanMembership.MembershipRole.MEMBER,
        status=ClanMembership.Status.ACCEPTED,
    ).exists()
    assert UserProfile.objects.get(user_id=user_id).career.startswith("DEMO-CAREER-")


@pytest.mark.django_db(transaction=True)
def test_membership_insert_started_after_seed_user_lock_waits_until_seed_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    catalog = seed_neon_dev.load_dev_synthetic_catalog()
    with transaction.atomic():
        load_catalog_data(catalog)
    user_id = demo_id("user", "1")
    destination_id = stable_reference_id("institutional-clan", catalog.clans[0]["key"])
    seed_holds_user_lock = Event()
    release_seed = Event()
    seed_committed = Event()
    insert_started = Event()
    insert_completed_after_seed = Event()
    insert_backend_pids: list[int] = []
    original_user_lock = seed_neon_dev._lock_synthetic_users

    def hold_seed_user_locks(user_ids: list[Any]) -> list[User]:
        users = original_user_lock(user_ids)
        seed_holds_user_lock.set()
        if not release_seed.wait(timeout=10):
            raise TimeoutError("Test did not release seed user locks")
        return users

    def insert_membership() -> None:
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    cursor.execute("SELECT pg_backend_pid()")
                    insert_backend_pids.append(cursor.fetchone()[0])
                insert_started.set()
                ClanMembership.objects.create(
                    user=User.objects.get(pk=user_id),
                    clan=Clan.all_objects.get(pk=destination_id),
                    role=ClanMembership.MembershipRole.MEMBER,
                    status=ClanMembership.Status.ACCEPTED,
                )
                insert_completed_after_seed.set()
        finally:
            connections.close_all()

    def run_seed() -> None:
        try:
            call_command(
                "seed_neon_dev",
                confirm_target="dev",
                as_of="2030-01-15T12:00:00+00:00",
                verbosity=0,
            )
        finally:
            seed_committed.set()
            connections.close_all()

    monkeypatch.setattr(seed_neon_dev, "_lock_synthetic_users", hold_seed_user_locks)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        seed = executor.submit(run_seed)
        assert seed_holds_user_lock.wait(timeout=10)
        insert = executor.submit(insert_membership)
        assert insert_started.wait(timeout=10)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [insert_backend_pids[0]],
                )
                activity = cursor.fetchone()
            if activity is not None and activity[0] == "Lock" and activity[1]:
                break
            sleep(0.01)
        else:
            release_seed.set()
            pytest.fail(
                f"Membership insert did not wait on seed user lock: {activity!r}"
            )
        assert not seed_committed.is_set()
        assert not insert_completed_after_seed.is_set()
        release_seed.set()
        seed.result(timeout=20)
        assert seed_committed.is_set()
        insert.result(timeout=20)
        assert insert_completed_after_seed.is_set()

    assert UserProfile.objects.get(user_id=user_id).career in DEV_CAREERS


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_refuses_human_edited_legacy_profile_without_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    profile = UserProfile.objects.order_by("user_id").first()
    assert profile is not None
    profile.career = "Manually edited career"
    profile.save(update_fields=["career"])
    legacy_count = Clan.objects.filter(
        name__startswith="Demo institutional clan"
    ).count()
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)

    with pytest.raises(CommandError, match="legacy demo profile or clan differs"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)

    profile.refresh_from_db()
    assert profile.career == "Manually edited career"
    assert (
        Clan.objects.filter(name__startswith="Demo institutional clan").count()
        == legacy_count
    )
    assert not Clan.objects.filter(name__in=DEV_CAREERS).exists()


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_refuses_partial_legacy_logs_without_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    ActionLog.objects.filter(pk=demo_id("action-log", "23")).delete()
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)

    with pytest.raises(CommandError, match="legacy seed is partial"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)

    assert not Clan.objects.filter(name__in=DEV_CAREERS).exists()
    assert (
        ActionLog.objects.filter(
            pk__in=[demo_id("action-log", str(index)) for index in range(23)]
        ).count()
        == 23
    )


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_refuses_edited_legacy_membership_without_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_legacy_neon_demo(monkeypatch)
    membership = ClanMembership.objects.order_by("user_id").first()
    assert membership is not None
    membership.role = ClanMembership.MembershipRole.LEADER
    membership.save(update_fields=["role"])
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)

    with pytest.raises(CommandError, match="institutional membership was edited"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)

    membership.refresh_from_db()
    assert membership.role == ClanMembership.MembershipRole.LEADER
    assert not Clan.objects.filter(name__in=DEV_CAREERS).exists()


@pytest.mark.django_db(transaction=True)
def test_neon_dev_command_rolls_back_catalog_if_demo_seed_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = seed_neon_dev.DEV_SYNTHETIC_CATALOG
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)

    def fail_seed(*_args: object, **_kwargs: object) -> None:
        raise CommandError("synthetic demo failure")

    monkeypatch.setattr(seed_neon_dev, "seed_demo_data", fail_seed)
    with pytest.raises(CommandError, match="synthetic demo failure"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)

    assert ActionCategory.objects.count() == 0
    assert ActionMaster.objects.count() == 0
    assert User.objects.count() == 0
