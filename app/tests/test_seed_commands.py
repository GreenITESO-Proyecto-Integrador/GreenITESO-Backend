"""PostgreSQL integration checks for the T11/T12 seed commands."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from green_iteso.accounts.management.commands.bootstrap_dev import demo_id
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands import release_catalog
from green_iteso.actions.management.commands.load_catalog import (
    load_catalog_file,
    stable_reference_id,
)
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


def test_product_candidate_remains_inactive_draft_without_clans() -> None:
    candidate = Path(__file__).resolve().parents[2] / "docs/catalog-candidate-v1.json"
    catalog = load_catalog_file(candidate)
    assert len(catalog.categories) == 3
    assert len(catalog.actions) == 3
    assert not catalog.clans
    assert all(not action["is_active"] for action in catalog.actions)


@pytest.mark.django_db(transaction=True)
def test_load_catalog_is_idempotent_and_preserves_existing_edits() -> None:
    """Two imports produce one row per stable code and do not overwrite Admin edits."""
    call_command("load_catalog", verbosity=0)
    category = ActionCategory.objects.get(code="draft-mobility")
    category.name = "Local reviewer name"
    category.save(update_fields=["name"])
    counts = (
        ActionCategory.objects.count(),
        ActionMaster.objects.count(),
        Clan.objects.count(),
    )

    call_command("load_catalog", verbosity=0)

    assert (
        ActionCategory.objects.count(),
        ActionMaster.objects.count(),
        Clan.objects.count(),
    ) == counts
    assert (
        ActionCategory.objects.get(code="draft-mobility").name == "Local reviewer name"
    )
    assert ActionMaster.objects.values_list("code", flat=True).distinct().count() == 3
    assert Clan.objects.filter(type=Clan.ClanType.INSTITUTIONAL).count() == 3


@pytest.mark.django_db(transaction=True)
def test_bootstrap_dev_is_idempotent_and_keeps_points_contribution_shape() -> None:
    """The complete synthetic graph can be loaded repeatedly without duplicates."""
    call_command("load_catalog", verbosity=0)
    unrelated_category = ActionCategory.objects.create(
        code="unrelated-local", name="Unrelated local action"
    )
    ActionMaster.objects.create(
        code="unrelated-local",
        category=unrelated_category,
        name="Unrelated local action",
        description="Must not be appropriated by the demo fixture.",
        points=99,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    options = {"as_of": "2030-01-15T12:00:00+00:00", "verbosity": 0}
    call_command("bootstrap_dev", **options)
    counts = {
        "users": User.objects.count(),
        "profiles": UserProfile.objects.count(),
        "memberships": ClanMembership.objects.count(),
        "campaigns": Campaign.objects.count(),
        "missions": Mission.objects.count(),
        "participants": CampaignParticipant.objects.count(),
        "progress": UserMissionProgress.objects.count(),
        "logs": ActionLog.objects.count(),
        "contributions": ActionLogMissionContribution.objects.count(),
    }
    call_command("bootstrap_dev", **options)

    assert counts == {
        "users": User.objects.count(),
        "profiles": UserProfile.objects.count(),
        "memberships": ClanMembership.objects.count(),
        "campaigns": Campaign.objects.count(),
        "missions": Mission.objects.count(),
        "participants": CampaignParticipant.objects.count(),
        "progress": UserMissionProgress.objects.count(),
        "logs": ActionLog.objects.count(),
        "contributions": ActionLogMissionContribution.objects.count(),
    }
    assert User.objects.count() == 20
    assert ActionLog.objects.filter(status=ActionLog.Status.APPROVED).exists()
    assert ActionLog.objects.filter(status=ActionLog.Status.PENDING_AUDIT).exists()
    assert ActionLog.objects.filter(
        status=ActionLog.Status.REJECTED, points_awarded__gt=0
    ).exists()
    assert ActionLogMissionContribution.objects.count() > 0
    assert not ActionLogMissionContribution.objects.exclude(
        action_log__status=ActionLog.Status.APPROVED
    ).exists()
    assert User.objects.filter(firebase_uid__isnull=True).count() == 20
    assert ActionMaster.objects.filter(code="unrelated-local").exists()
    assert not ActionLog.objects.filter(action__code="unrelated-local").exists()
    assert not ActionLog.objects.filter(
        status=ActionLog.Status.REJECTED, points_awarded__lte=0
    ).exists()
    assert sum(UserProfile.objects.values_list("total_points", flat=True)) == sum(
        ActionLog.objects.filter(status=ActionLog.Status.APPROVED).values_list(
            "points_awarded", flat=True
        )
    )
    for profile in UserProfile.objects.select_related("user"):
        assert profile.total_points == sum(
            ActionLog.objects.filter(
                user=profile.user, status=ActionLog.Status.APPROVED
            ).values_list("points_awarded", flat=True)
        )
        assert profile.available_points == profile.total_points
    for clan in Clan.objects.all():
        logs = ActionLog.objects.filter(status=ActionLog.Status.APPROVED)
        if clan.type == Clan.ClanType.INSTITUTIONAL:
            logs = logs.filter(institutional_clan=clan)
        else:
            logs = logs.filter(credited_private_clan=clan)
        assert clan.total_points == sum(logs.values_list("points_awarded", flat=True))


@pytest.mark.django_db(transaction=True)
def test_invalid_catalog_input_is_atomic(tmp_path: Path) -> None:
    """Invalid values fail before writes, leaving a clean database."""
    payload = {
        "schema_version": 1,
        "status": "DRAFT",
        "categories": [{"code": "draft-test", "name": "Draft test"}],
        "actions": [
            {
                "code": "draft-invalid",
                "category_code": "draft-test",
                "name": "Invalid",
                "description": "Invalid fixture",
                "points": 0,
                "daily_limit": 1,
                "validation_type": "NONE",
            }
        ],
        "institutional_clans": [],
    }
    path = tmp_path / "invalid-catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CommandError, match="positive integer"):
        call_command("load_catalog", input=path, verbosity=0)

    assert ActionCategory.objects.count() == 0
    assert ActionMaster.objects.count() == 0
    assert Clan.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_demo_guard_rejects_deployed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demo command fails explicitly before any deployed database write."""
    monkeypatch.setenv("DJANGO_ENV", "staging")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(settings, "DEBUG", False)

    with pytest.raises(CommandError, match="local-only"):
        call_command("bootstrap_dev", verbosity=0)

    assert User.objects.count() == 0
    assert ActionCategory.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_catalog_guard_rejects_deployed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provisional reference fixture cannot be loaded into a cloud target."""
    monkeypatch.setattr(settings, "DEPLOYED", True)

    with pytest.raises(CommandError, match="deployed environment"):
        call_command("load_catalog", verbosity=0)

    assert ActionCategory.objects.count() == 0
    assert Clan.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_catalog_guard_rejects_non_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(settings.DATABASES["default"], "HOST", "shared.neon.tech")

    with pytest.raises(CommandError, match="local PostgreSQL host"):
        call_command("load_catalog", verbosity=0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["category", "action", "clan"])
def test_catalog_rejects_fixture_code_with_unrelated_id(kind: str) -> None:
    if kind == "clan":
        Clan.objects.create(
            id=stable_reference_id("institutional-clan", "draft-engineering"),
            name="Unrelated private clan",
            type=Clan.ClanType.INSTITUTIONAL,
            privacy=Clan.Privacy.PRIVATE_INVITE,
        )
        with pytest.raises(CommandError, match="identity collision"):
            call_command("load_catalog", verbosity=0)
        return

    category = ActionCategory.objects.create(
        id=(
            uuid.uuid4()
            if kind == "category"
            else stable_reference_id("category", "draft-mobility")
        ),
        code="draft-mobility",
        name="Draft mobility",
    )
    if kind == "action":
        ActionMaster.objects.create(
            id=uuid.uuid4(),
            code="draft-bike-trip",
            category=category,
            name="Unrelated action",
            description="Must not be adopted by the draft fixture.",
            points=1,
            daily_limit=1,
            validation_type=ActionMaster.ValidationType.NONE,
        )

    with pytest.raises(CommandError, match="identity collision"):
        call_command("load_catalog", verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_catalog_rejects_clan_name_collision_with_a_different_id() -> None:
    Clan.objects.create(
        id=uuid.uuid4(),
        name="Draft Engineering",
        type=Clan.ClanType.INSTITUTIONAL,
        privacy=Clan.Privacy.PUBLIC,
    )

    with pytest.raises(CommandError, match="Catalog clan name collision"):
        call_command("load_catalog", verbosity=0)

    assert ActionCategory.objects.count() == 0
    assert ActionMaster.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("command_name", ["load_catalog", "bootstrap_dev"])
def test_seed_commands_reject_tls_even_when_host_looks_local(
    monkeypatch: pytest.MonkeyPatch, command_name: str
) -> None:
    class EncryptedCursor:
        def __enter__(self) -> EncryptedCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _query: str) -> None:
            return None

        def fetchone(self) -> tuple[bool]:
            return (True,)

    monkeypatch.setitem(settings.DATABASES["default"], "HOST", "db")
    monkeypatch.setattr(connection, "cursor", lambda: EncryptedCursor())
    if command_name == "bootstrap_dev":
        monkeypatch.setenv("DJANGO_ENV", "dev")
        monkeypatch.setattr(settings, "DEPLOYED", False)

    with pytest.raises(CommandError, match="unencrypted local PostgreSQL"):
        call_command(command_name, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_demo_guard_rejects_non_local_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit dev flag cannot turn a shared cloud target into a demo DB."""
    monkeypatch.setitem(settings.DATABASES["default"], "HOST", "shared.neon.tech")

    with pytest.raises(CommandError, match="local PostgreSQL host"):
        call_command("bootstrap_dev", verbosity=0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("collision", ["user", "clan"])
def test_bootstrap_rejects_unrelated_identity_collisions(collision: str) -> None:
    if collision == "user":
        User.objects.create_user(email="demo-01@example.invalid")
    else:
        Clan.objects.create(name="Demo private clan 01", type=Clan.ClanType.PRIVATE)
    before = (User.objects.count(), Clan.objects.count())
    with pytest.raises(CommandError, match="collision"):
        call_command("bootstrap_dev", verbosity=0)
    assert (User.objects.count(), Clan.objects.count()) == before
    assert ActionCategory.objects.count() == 0
    assert ActionLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("collision", ["user", "clan"])
def test_bootstrap_rejects_deterministic_id_collisions(collision: str) -> None:
    if collision == "user":
        User.objects.create_user(
            id=demo_id("user", "1"), email="unrelated@example.invalid"
        )
    else:
        Clan.objects.create(
            id=demo_id("clan", "private-01"),
            name="Unrelated private clan",
            type=Clan.ClanType.PRIVATE,
            privacy=Clan.Privacy.PRIVATE_INVITE,
        )

    before = (User.objects.count(), Clan.objects.count())
    with pytest.raises(CommandError, match="identity collision"):
        call_command("bootstrap_dev", verbosity=0)

    assert (User.objects.count(), Clan.objects.count()) == before
    assert ActionCategory.objects.count() == 0
    assert ActionLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_bootstrap_does_not_recreate_reversed_contributions() -> None:
    call_command("bootstrap_dev", verbosity=0)
    contribution = ActionLogMissionContribution.objects.select_related(
        "action_log"
    ).first()
    log = contribution.action_log
    log.status = ActionLog.Status.REJECTED
    log.save(update_fields=["status"])
    contribution.delete()
    call_command("bootstrap_dev", verbosity=0)
    log.refresh_from_db()
    assert log.status == ActionLog.Status.REJECTED
    assert not log.mission_contributions.exists()


@pytest.mark.django_db(transaction=True)
def test_release_catalog_fails_closed_without_fixture_or_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(release_catalog, "ensure_release_target", lambda _target: None)
    monkeypatch.setattr(release_catalog, "APPROVED_CATALOG", tmp_path / "missing.json")
    monkeypatch.delenv("NEON_DEV_APPROVED_CATALOG_SHA256", raising=False)
    with pytest.raises(CommandError, match="SHA-256"):
        call_command("release_catalog", confirm_target="dev", verbosity=0)

    monkeypatch.setenv("NEON_DEV_APPROVED_CATALOG_SHA256", "a" * 64)
    with pytest.raises(CommandError, match="read"):
        call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert ActionCategory.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_release_catalog_rejects_fixture_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = tmp_path / "tampered.json"
    fixture.write_bytes(b"{}")
    monkeypatch.setattr(release_catalog, "APPROVED_CATALOG", fixture)
    monkeypatch.setenv("NEON_DEV_APPROVED_CATALOG_SHA256", "0" * 64)
    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(release_catalog, "ensure_release_target", lambda _target: None)

    with pytest.raises(CommandError, match="does not match"):
        call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert ActionCategory.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_release_catalog_is_idempotent_and_catalog_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = json.dumps(
        {
            "schema_version": 1,
            "status": "APPROVED",
            "approval": {
                "approved_by": "Synthetic test only",
                "reference": "test-payload-not-product-approval",
                "approved_at": "2026-01-01T00:00:00Z",
            },
            "categories": [{"code": "approved-mobility", "name": "Test Mobility"}],
            "actions": [
                {
                    "code": "approved-bike",
                    "category_code": "approved-mobility",
                    "name": "Test Bike",
                    "description": "Synthetic test row",
                    "points": 1,
                    "daily_limit": 1,
                    "validation_type": "NONE",
                }
            ],
            "institutional_clans": [],
        }
    ).encode()
    fixture = tmp_path / "synthetic-approved-test.json"
    fixture.write_bytes(content)
    monkeypatch.setattr(release_catalog, "APPROVED_CATALOG", fixture)
    monkeypatch.setenv(
        "NEON_DEV_APPROVED_CATALOG_SHA256", hashlib.sha256(content).hexdigest()
    )
    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(release_catalog, "ensure_release_target", lambda _target: None)
    monkeypatch.setattr(release_catalog, "ensure_tls_connection", lambda: None)

    call_command("release_catalog", confirm_target="dev", verbosity=0)
    call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert ActionCategory.objects.filter(code="approved-mobility").count() == 1
    assert ActionMaster.objects.filter(code="approved-bike").count() == 1
    assert User.objects.count() == 0
    assert ActionLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_release_catalog_rejects_existing_edits_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = json.dumps(
        {
            "schema_version": 1,
            "status": "APPROVED",
            "approval": {
                "approved_by": "Synthetic test only",
                "reference": "test-payload-not-product-approval",
                "approved_at": "2026-01-01T00:00:00Z",
            },
            "categories": [
                {"code": "approved-mobility", "name": "Test Mobility"},
                {"code": "approved-water", "name": "Test Water"},
            ],
            "actions": [],
            "institutional_clans": [],
        }
    ).encode()
    fixture = tmp_path / "synthetic-approved-test.json"
    fixture.write_bytes(content)
    monkeypatch.setattr(release_catalog, "APPROVED_CATALOG", fixture)
    monkeypatch.setenv(
        "NEON_DEV_APPROVED_CATALOG_SHA256", hashlib.sha256(content).hexdigest()
    )
    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(release_catalog, "ensure_release_target", lambda _target: None)
    monkeypatch.setattr(release_catalog, "ensure_tls_connection", lambda: None)
    ActionCategory.objects.create(
        id=stable_reference_id("category", "approved-mobility"),
        code="approved-mobility",
        name="Edited test value",
    )

    with pytest.raises(CommandError, match="differs"):
        call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert not ActionCategory.objects.filter(code="approved-water").exists()
    assert ActionMaster.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_release_catalog_rolls_back_if_row_changes_after_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = json.dumps(
        {
            "schema_version": 1,
            "status": "APPROVED",
            "approval": {
                "approved_by": "Synthetic test only",
                "reference": "test-payload-not-product-approval",
                "approved_at": "2026-01-01T00:00:00Z",
            },
            "categories": [{"code": "approved-water", "name": "Test Water"}],
            "actions": [],
            "institutional_clans": [],
        }
    ).encode()
    fixture = tmp_path / "synthetic-approved-test.json"
    fixture.write_bytes(content)
    monkeypatch.setattr(release_catalog, "APPROVED_CATALOG", fixture)
    monkeypatch.setenv(
        "NEON_DEV_APPROVED_CATALOG_SHA256", hashlib.sha256(content).hexdigest()
    )
    monkeypatch.setenv("DJANGO_ENV", "dev")
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(release_catalog, "ensure_release_target", lambda _target: None)
    monkeypatch.setattr(release_catalog, "ensure_tls_connection", lambda: None)
    original_load = release_catalog.load_catalog_data

    def intervening_change(catalog: object) -> tuple[int, int]:
        result = original_load(catalog)
        ActionCategory.objects.filter(code="approved-water").update(name="Conflict")
        return result

    monkeypatch.setattr(release_catalog, "load_catalog_data", intervening_change)
    with pytest.raises(CommandError, match="differs"):
        call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert not ActionCategory.objects.filter(code="approved-water").exists()


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_release_catalog_target_guard_checks_role_host_tls_configuration(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    from green_iteso.settings.neon_endpoints import canonical_neon_host

    monkeypatch.setenv("DJANGO_ENV", environment)
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(settings, "CONNECTION_ROLE", "app")
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "HOST",
        canonical_neon_host(environment, pooled=True),
    )
    monkeypatch.setitem(
        settings.DATABASES["default"], "USER", f"greeniteso_{environment}_app"
    )
    monkeypatch.setitem(
        settings.DATABASES["default"], "OPTIONS", {"sslmode": "verify-full"}
    )
    release_catalog.ensure_release_target(environment)

    monkeypatch.setitem(settings.DATABASES["default"], "USER", "wrong-role")
    with pytest.raises(CommandError, match="role"):
        release_catalog.ensure_release_target(environment)
    monkeypatch.setitem(
        settings.DATABASES["default"], "USER", f"greeniteso_{environment}_app"
    )

    monkeypatch.setitem(settings.DATABASES["default"], "HOST", "wrong.neon.tech")
    with pytest.raises(CommandError, match="branch"):
        release_catalog.ensure_release_target(environment)
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "HOST",
        canonical_neon_host(environment, pooled=True),
    )
    monkeypatch.setitem(
        settings.DATABASES["default"], "OPTIONS", {"sslmode": "require"}
    )
    with pytest.raises(CommandError, match="verify-full"):
        release_catalog.ensure_release_target(environment)

    monkeypatch.setitem(
        settings.DATABASES["default"], "OPTIONS", {"sslmode": "verify-full"}
    )
    monkeypatch.setenv("DJANGO_ENV", "wrong")
    with pytest.raises(CommandError, match="DJANGO_ENV"):
        release_catalog.ensure_release_target(environment)
