"""Safety checks for the separately gated shared Neon-dev demo seed."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction

from green_iteso.accounts.management.commands import seed_neon_dev
from green_iteso.accounts.management.commands.bootstrap_dev import (
    create_demo_users,
    seed_demo_data,
)
from green_iteso.accounts.models import Clan, User, UserProfile
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
from green_iteso.settings.neon_endpoints import CANONICAL_NEON_ENDPOINTS


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
        seed_neon_dev.APPROVED_CATALOG_SHA256_ENV,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


@pytest.mark.django_db(transaction=True)
def test_neon_dev_seed_rejects_draft_catalog_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "draft.json"
    path.write_text(DEFAULT_CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(seed_neon_dev, "APPROVED_CATALOG", path)
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(
        connection,
        "cursor",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )

    with pytest.raises(CommandError, match="APPROVED"):
        call_command("seed_neon_dev", confirm_target="dev", verbosity=0)


def test_neon_dev_seed_requires_explicit_target_confirmation() -> None:
    with pytest.raises(CommandError, match="--confirm-target dev"):
        call_command("seed_neon_dev", confirm_target="", verbosity=0)


def test_neon_dev_seed_requires_independently_pinned_catalog_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog_approved.json"
    path.write_text(json.dumps(approved_payload()), encoding="utf-8")
    monkeypatch.setattr(seed_neon_dev, "APPROVED_CATALOG", path)
    monkeypatch.delenv(seed_neon_dev.APPROVED_CATALOG_SHA256_ENV, raising=False)
    with pytest.raises(CommandError, match="SHA-256"):
        seed_neon_dev.load_approved_catalog()
    monkeypatch.setenv(seed_neon_dev.APPROVED_CATALOG_SHA256_ENV, "0" * 64)
    with pytest.raises(CommandError, match="does not match"):
        seed_neon_dev.load_approved_catalog()


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
    monkeypatch.setitem(
        settings.DATABASES["default"], "OPTIONS", {"sslmode": "verify-full"}
    )

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

    class UnencryptedCursor:
        def __enter__(self) -> UnencryptedCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _query: str) -> None:
            return None

        def fetchone(self) -> tuple[bool]:
            return (False,)

    monkeypatch.setattr(connection, "cursor", lambda: UnencryptedCursor())

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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog_approved.json"
    path.write_text(json.dumps(approved_payload()), encoding="utf-8")
    monkeypatch.setattr(seed_neon_dev, "APPROVED_CATALOG", path)
    pin_catalog_digest(path, monkeypatch)
    monkeypatch.setattr(seed_neon_dev, "ensure_neon_dev_target", lambda: None)
    monkeypatch.setattr(seed_neon_dev, "ensure_tls_connection", lambda: None)
    options = {"confirm_target": "dev", "as_of": "2030-01-15T12:00:00+00:00"}

    call_command("seed_neon_dev", **options, verbosity=0)
    first_counts = (User.objects.count(), ActionLog.objects.count())
    call_command("seed_neon_dev", **options, verbosity=0)

    assert first_counts == (20, 24)
    assert (User.objects.count(), ActionLog.objects.count()) == first_counts
    assert set(User.objects.values_list("role", flat=True)) == {User.Role.STUDENT}
    assert not ActionLogMissionContribution.objects.exclude(
        action_log__status=ActionLog.Status.APPROVED
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_neon_dev_command_rolls_back_catalog_if_demo_seed_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog_approved.json"
    path.write_text(json.dumps(approved_payload()), encoding="utf-8")
    monkeypatch.setattr(seed_neon_dev, "APPROVED_CATALOG", path)
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
