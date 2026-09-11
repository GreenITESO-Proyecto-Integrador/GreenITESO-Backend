"""PostgreSQL checks for the draft T8 recovery integrity verifier."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from stat import S_IMODE

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from green_iteso.accounts.management.commands import db_recovery_verify
from green_iteso.accounts.models import Clan, User
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster


def _create_history() -> ActionLog:
    """Create one synthetic historical log and its stable pre-T marker."""
    user = User.objects.create_user(email="recovery-verifier@example.invalid")
    clan = Clan.objects.create(
        name="Recovery verifier clan", type=Clan.ClanType.INSTITUTIONAL
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
        validation_mode=ActionMaster.ValidationMode.DECLARATIVE_BUTTON,
    )
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="recovery-pre-t",
        points_awarded=7,
        status=ActionLog.Status.APPROVED,
    )


def _capture_baseline(tmp_path: Path) -> tuple[Path, ActionLog, str]:
    marker = _create_history()
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
    baseline, _marker, _post_marker_id = _capture_baseline(tmp_path)
    content = json.loads(baseline.read_text(encoding="utf-8"))
    rendered = json.dumps(content)
    assert "recovery-verifier@example.invalid" not in rendered
    assert "Synthetic recovery check action" not in rendered


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
