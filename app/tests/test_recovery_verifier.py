"""PostgreSQL checks for the draft T8 recovery integrity verifier."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from stat import S_IMODE
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.utils import timezone

from green_iteso.accounts.management.commands import db_recovery_verify
from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster


def _create_history() -> ActionLog:
    """Create one synthetic historical log and its stable pre-T marker."""
    user = User.objects.create_user(email="recovery-verifier@example.invalid")
    clan = Clan.objects.create(
        name="Recovery verifier clan", type=Clan.ClanType.INSTITUTIONAL
    )
    private_clan = Clan.objects.create(
        name="Recovery verifier private clan",
        type=Clan.ClanType.PRIVATE,
        privacy=Clan.Privacy.PRIVATE_INVITE,
    )
    category = ActionCategory.objects.create(
        code="recovery-verifier", name="Recovery verifier"
    )
    action = ActionMaster.objects.create(
        code="recovery-verifier",
        category=category,
        name="Recovery verifier",
        description="Synthetic recovery check action",
        points=7,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
    )
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        credited_private_clan=private_clan,
        idempotency_key="recovery-pre-t",
        points_awarded=7,
        status=ActionLog.Status.APPROVED,
    )


def _capture_baseline(tmp_path: Path) -> tuple[Path, ActionLog, str]:
    marker = _create_history()
    Clan.all_objects.create(
        name="Soft-deleted recovery verifier clan",
        type=Clan.ClanType.INSTITUTIONAL,
        deleted_at=timezone.now(),
    )
    UserProfile.objects.create(
        user=marker.user,
        career="Recovery verifier career",
        employee_id="synthetic-private-employee-id",
        microsoft_group_ids=["synthetic-private-group-id"],
    )
    post_marker_id = str(uuid.uuid4())
    baseline = tmp_path / "recovery-baseline.json"
    call_command(
        "db_recovery_verify",
        write_baseline=baseline,
        pre_marker_id=str(marker.pk),
        post_marker_id=post_marker_id,
        timeout=5,
        verbosity=0,
    )
    return baseline, marker, post_marker_id


@pytest.mark.django_db
def test_recovery_fk_queries_follow_model_table_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(db_recovery_verify.connection, "cursor", lambda: cursor)

    model_tables = {
        db_recovery_verify.ActionLog: "renamed_action_log",
        db_recovery_verify.User: "renamed_user",
        db_recovery_verify.ActionMaster: "renamed_action_master",
        db_recovery_verify.Clan: "renamed_clan",
        db_recovery_verify.Campaign: "renamed_campaign",
    }
    for model, table_name in model_tables.items():
        monkeypatch.setattr(model._meta, "db_table", table_name)

    quoted_names: list[str] = []

    def quote_name(value: str) -> str:
        quoted_names.append(value)
        return f'"{value}"'

    monkeypatch.setattr(db_recovery_verify.connection.ops, "quote_name", quote_name)

    fk_orphans = db_recovery_verify._fk_orphans  # pylint: disable=protected-access
    assert fk_orphans() == {
        "user": 0,
        "action": 0,
        "institutional_clan": 0,
        "credited_private_clan": 0,
        "campaign": 0,
        "reviewed_by": 0,
    }
    assert set(quoted_names) == set(model_tables.values())
    rendered_queries = [call.args[0] for call in cursor.execute.call_args_list]
    for table_name in model_tables.values():
        assert any(f'"{table_name}"' in query for query in rendered_queries)


@pytest.mark.django_db(transaction=True)
def test_recovery_baseline_matches_unchanged_history(tmp_path: Path) -> None:
    baseline, _marker, _post_marker_id = _capture_baseline(tmp_path)

    call_command("db_recovery_verify", baseline=baseline, timeout=5, verbosity=0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("change", "diagnosis"),
    [
        ("count", "TABLE_COUNT_MISMATCH"),
        ("points", "ACTION_LOG_STATUS_POINTS_MISMATCH"),
        ("history", "ACTION_LOG_ATTRIBUTION_MISMATCH"),
        ("same_count", "TABLE_CONTENT_MISMATCH"),
        ("profile", "TABLE_CONTENT_MISMATCH"),
        ("soft_deleted_clan", "TABLE_CONTENT_MISMATCH"),
        ("idempotency", "ACTION_LOG_HISTORY_MISMATCH"),
    ],
)
def test_recovery_reports_count_points_and_history_mismatches(
    tmp_path: Path,
    change: str,
    diagnosis: str,
) -> None:
    baseline, marker, _post_marker_id = _capture_baseline(tmp_path)
    if change == "count":
        User.objects.create_user(email="recovery-count-change@example.invalid")
    elif change == "points":
        marker.points_awarded = 99
        marker.save(update_fields=["points_awarded"])
    elif change == "same_count":
        category = ActionCategory.objects.get(code="recovery-verifier")
        category.name = "Changed but row count is unchanged"
        category.save(update_fields=["name"])
    elif change == "profile":
        profile = UserProfile.objects.get(user=marker.user)
        profile.employee_id = "changed-same-count"
        profile.save(update_fields=["employee_id"])
    elif change == "soft_deleted_clan":
        clan = Clan.all_objects.get(name="Soft-deleted recovery verifier clan")
        clan.description = "Changed while remaining soft-deleted"
        clan.save(update_fields=["description"])
    elif change == "idempotency":
        marker.idempotency_key = "recovery-pre-t-modified"
        marker.save(update_fields=["idempotency_key"])
    else:
        second_clan = Clan.objects.create(
            name="Recovery verifier second clan", type=Clan.ClanType.INSTITUTIONAL
        )
        marker.institutional_clan = second_clan
        marker.save(update_fields=["institutional_clan"])

    with pytest.raises(CommandError, match=diagnosis):
        call_command("db_recovery_verify", baseline=baseline, timeout=5, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_recovery_rejects_post_t_marker(tmp_path: Path) -> None:
    baseline, marker, post_marker_id = _capture_baseline(tmp_path)
    post_marker = ActionLog.objects.create(
        pk=post_marker_id,
        user=marker.user,
        action=marker.action,
        institutional_clan=marker.institutional_clan,
        idempotency_key="recovery-post-t",
        points_awarded=7,
        status=ActionLog.Status.APPROVED,
    )
    assert str(post_marker.pk) == str(uuid.UUID(post_marker_id))

    with pytest.raises(CommandError, match="POST_MARKER_PRESENT"):
        call_command("db_recovery_verify", baseline=baseline, timeout=5, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_recovery_baseline_contains_no_personal_fields(tmp_path: Path) -> None:
    baseline, marker, _post_marker_id = _capture_baseline(tmp_path)
    content = json.loads(baseline.read_text(encoding="utf-8"))
    rendered = json.dumps(content)
    assert "recovery-verifier@example.invalid" not in rendered
    assert "Synthetic recovery check action" not in rendered
    assert "synthetic-private-employee-id" not in rendered
    assert "synthetic-private-group-id" not in rendered
    assert str(marker.institutional_clan_id) not in rendered
    assert str(marker.credited_private_clan_id) not in rendered


@pytest.mark.django_db(transaction=True)
def test_recovery_detects_equal_total_clan_reassignment(tmp_path: Path) -> None:
    marker = _create_history()
    second_clan = Clan.objects.create(
        name="Recovery verifier equal clan", type=Clan.ClanType.INSTITUTIONAL
    )
    second_user = User.objects.create_user(email="recovery-equal@example.invalid")
    second_log = ActionLog.objects.create(
        user=second_user,
        action=marker.action,
        institutional_clan=second_clan,
        idempotency_key="recovery-equal",
        points_awarded=marker.points_awarded,
        status=marker.status,
    )
    post_marker_id = str(uuid.uuid4())
    baseline = tmp_path / "recovery-equal-baseline.json"
    call_command(
        "db_recovery_verify",
        write_baseline=baseline,
        pre_marker_id=str(marker.pk),
        post_marker_id=post_marker_id,
        timeout=5,
        verbosity=0,
    )

    original_clan = marker.institutional_clan
    marker.institutional_clan = second_clan
    marker.save(update_fields=["institutional_clan"])
    second_log.institutional_clan = original_clan
    second_log.save(update_fields=["institutional_clan"])

    with pytest.raises(CommandError, match="ACTION_LOG_HISTORY_MISMATCH"):
        call_command("db_recovery_verify", baseline=baseline, timeout=5, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_recovery_rejects_nonzero_fk_orphan_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _create_history()
    baseline = tmp_path / "recovery-orphan-baseline.json"
    monkeypatch.setattr(
        db_recovery_verify,
        "_fk_orphans",
        lambda: {"user": 1},
    )

    with pytest.raises(CommandError, match="referencias foráneas huérfanas"):
        call_command(
            "db_recovery_verify",
            write_baseline=baseline,
            pre_marker_id=str(marker.pk),
            post_marker_id=str(uuid.uuid4()),
            timeout=5,
            verbosity=0,
        )


@pytest.mark.django_db(transaction=True)
def test_recovery_does_not_overwrite_baseline_and_uses_private_mode(
    tmp_path: Path,
) -> None:
    baseline, marker, post_marker_id = _capture_baseline(tmp_path)
    original = baseline.read_bytes()
    assert S_IMODE(baseline.stat().st_mode) == 0o600

    with pytest.raises(CommandError, match="BASELINE_WRITE_FAILURE"):
        call_command(
            "db_recovery_verify",
            write_baseline=baseline,
            pre_marker_id=str(marker.pk),
            post_marker_id=post_marker_id,
            timeout=5,
            verbosity=0,
        )
    assert baseline.read_bytes() == original


@pytest.mark.django_db(transaction=True)
def test_recovery_snapshot_transaction_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def attempt_write(*_args: object) -> dict[str, object]:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE recovery_write_probe(id integer)")
        return {}

    monkeypatch.setattr(db_recovery_verify, "_snapshot", attempt_write)
    with pytest.raises(DatabaseError):
        db_recovery_verify._read_snapshot(
            5,
            expected=None,
            pre_marker=str(uuid.uuid4()),
            post_marker=str(uuid.uuid4()),
        )


@pytest.mark.django_db(transaction=True)
def test_recovery_total_timeout_covers_baseline_file_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def slow_read(_path: Path) -> dict[str, object]:
        time.sleep(0.2)
        return {}

    monkeypatch.setattr(db_recovery_verify, "_load_baseline", slow_read)
    with pytest.raises(CommandError, match="diagnostico: TIMEOUT"):
        call_command(
            "db_recovery_verify",
            baseline=tmp_path / "unused.json",
            timeout=0.1,
            verbosity=0,
        )


@pytest.mark.django_db(transaction=True)
def test_recovery_total_timeout_covers_baseline_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker_ids = (str(uuid.uuid4()), str(uuid.uuid4()))
    baseline_path = tmp_path / "interrupted-baseline.json"
    monkeypatch.setattr(
        db_recovery_verify,
        "_read_snapshot",
        lambda *_args: (
            {"markers": {"pre_present": True, "post_present": False}},
            [],
        ),
    )

    real_fdopen = db_recovery_verify.os.fdopen

    class SlowHandle:
        def __init__(self, file_descriptor: int, mode: str, *, encoding: str) -> None:
            self.handle = real_fdopen(file_descriptor, mode, encoding=encoding)

        def __enter__(self) -> SlowHandle:
            self.handle.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)

        def write(self, payload: str) -> int:
            self.handle.write(payload[:8])
            self.handle.flush()
            time.sleep(0.2)
            return len(payload)

    monkeypatch.setattr(db_recovery_verify.os, "fdopen", SlowHandle)
    with pytest.raises(CommandError, match="diagnostico: TIMEOUT"):
        call_command(
            "db_recovery_verify",
            write_baseline=baseline_path,
            pre_marker_id=marker_ids[0],
            post_marker_id=marker_ids[1],
            timeout=0.1,
            verbosity=0,
        )
    assert not baseline_path.exists()
