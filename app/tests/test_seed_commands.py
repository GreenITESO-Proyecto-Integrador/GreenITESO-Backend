"""PostgreSQL integration checks for the T11/T12 seed commands."""

from __future__ import annotations

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
from green_iteso.actions.management.commands.load_catalog import stable_reference_id
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
