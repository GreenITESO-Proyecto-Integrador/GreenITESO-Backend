"""PostgreSQL integration checks for the T11/T12 seed commands."""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from green_iteso.accounts.management.commands.bootstrap_dev import (
    LEGACY_DEMO_CAMPAIGN_DESCRIPTION,
    create_demo_users,
    demo_id,
    refresh_clan_totals,
    refresh_profile_totals,
    seed_demo_data,
)
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands import release_catalog
from green_iteso.actions.management.commands.load_catalog import (
    DEFAULT_CATALOG,
    LOCAL_DATABASE_HOSTS,
    load_catalog_content,
    load_catalog_data,
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
    """Keep the exact Product review candidate separate from release fixtures."""
    candidate = Path(__file__).resolve().parents[2] / "docs/catalog-candidate-v1.json"
    proposal = candidate.with_name("catalog-proposal.md")
    content = candidate.read_bytes()
    payload = json.loads(content)
    catalog = load_catalog_file(candidate)
    assert payload["status"] == "DRAFT"
    assert "approval" not in payload
    assert f"`{hashlib.sha256(content).hexdigest()}`" in proposal.read_text()
    assert len(catalog.categories) == 3
    assert len(catalog.actions) == 3
    assert not catalog.clans
    assert all(not action["is_active"] for action in catalog.actions)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("categories", "description"),
        ("categories", "icon"),
        ("institutional_clans", "description"),
    ],
)
def test_draft_catalog_rejects_nontext_optional_fields(
    section: str, field: str
) -> None:
    payload = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    payload[section][0][field] = {"unexpected": "object"}
    with pytest.raises(CommandError, match=field):
        load_catalog_content(json.dumps(payload), source="test")


@pytest.mark.django_db(transaction=True)
def test_demo_seed_keeps_action_ids_stable_after_activation_change() -> None:
    catalog = load_catalog_file(DEFAULT_CATALOG)
    load_catalog_data(catalog)
    as_of = datetime(2030, 1, 15, 12, tzinfo=UTC)
    seed_demo_data(catalog, as_of)
    assert ActionMaster.objects.get(code=catalog.actions[0]["code"]).is_active
    ActionMaster.objects.filter(code=catalog.actions[0]["code"]).update(is_active=False)
    assert seed_demo_data(catalog, as_of).created_logs == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_dev", [False, True])
def test_demo_seed_uses_loggable_actions_and_realistic_pending_audits(
    shared_dev: bool,
) -> None:
    fixture = (
        DEFAULT_CATALOG.with_name("catalog_dev_synthetic_v1.json")
        if shared_dev
        else DEFAULT_CATALOG
    )
    catalog = load_catalog_file(fixture)
    load_catalog_data(catalog)
    as_of = datetime(2030, 1, 15, 12, tzinfo=UTC)
    seed_demo_data(catalog, as_of, shared_dev=shared_dev)

    assert not Mission.objects.filter(action__is_active=False).exists()
    assert not ActionLog.objects.filter(action__is_active=False).exists()
    pending = ActionLog.objects.filter(status=ActionLog.Status.PENDING_AUDIT)
    assert pending.exists()
    assert not pending.exclude(
        action__validation_type=ActionMaster.ValidationType.PHOTO
    ).exists()
    assert not pending.filter(evidence_object_key="").exists()
    assert seed_demo_data(catalog, as_of, shared_dev=shared_dev).created_logs == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_dev", [False, True])
def test_demo_seed_credits_preserved_profile_clan_outside_catalog(
    shared_dev: bool,
) -> None:
    catalog = load_catalog_file(DEFAULT_CATALOG)
    load_catalog_data(catalog)
    users, _ = create_demo_users(shared_dev=shared_dev)
    clan = Clan.objects.create(
        name="Admin-selected clan", type=Clan.ClanType.INSTITUTIONAL
    )
    profile = UserProfile.objects.create(
        user=users[0], institutional_clan=clan, career="Admin-edited career"
    )
    historical_clan = Clan.objects.create(
        name="Historical institutional membership", type=Clan.ClanType.INSTITUTIONAL
    )
    historical_membership = ClanMembership.objects.create(
        user=users[0], clan=historical_clan
    )
    joined_at = historical_membership.joined_at

    for _ in range(2):
        seed_demo_data(
            catalog, datetime(2030, 1, 15, 12, tzinfo=UTC), shared_dev=shared_dev
        )
        assert set(
            ClanMembership.objects.filter(
                user=users[0], clan__type=Clan.ClanType.INSTITUTIONAL
            ).values_list("clan_id", flat=True)
        ) == {clan.pk, historical_clan.pk}
        historical_membership.refresh_from_db()
        assert historical_membership.joined_at == joined_at

    profile.refresh_from_db()
    clan.refresh_from_db()
    assert profile.institutional_clan_id == clan.pk
    assert profile.career == "Admin-edited career"
    points = sum(
        ActionLog.objects.filter(
            institutional_clan=clan, status=ActionLog.Status.APPROVED
        ).values_list("points_awarded", flat=True)
    )
    assert points > 0
    assert clan.total_points == points


@pytest.mark.django_db(transaction=True)
def test_demo_seed_credits_clan_snapshot_after_soft_delete() -> None:
    clan = Clan.objects.create(name="Dissolved demo", type=Clan.ClanType.PRIVATE)
    Clan.all_objects.filter(pk=clan.pk).update(deleted_at=timezone.now())

    refresh_clan_totals([], [clan], {clan.pk: 7}, shared_dev=True)

    assert Clan.all_objects.get(pk=clan.pk).total_points == 7


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("projection", ["profile", "clan"])
def test_local_demo_recomputation_preserves_concurrent_api_credit(
    projection: str,
) -> None:
    """Reproduce a credit arriving between the aggregate read and absolute write."""
    catalog = load_catalog_file(DEFAULT_CATALOG)
    load_catalog_data(catalog)
    seed_demo_data(catalog, datetime(2030, 1, 15, 12, tzinfo=UTC))
    profile = UserProfile.objects.get(user_id=demo_id("user", "1"))
    clan = profile.institutional_clan
    target = profile if projection == "profile" else clan
    before = target.total_points
    writing = Event()
    attempted = Event()
    credited = Event()

    def credit() -> None:
        close_old_connections()
        try:
            assert writing.wait(timeout=5)
            attempted.set()
            type(target).objects.filter(pk=target.pk).update(
                total_points=F("total_points") + 5
            )
            credited.set()
        finally:
            close_old_connections()

    def pause_before_absolute_write() -> None:
        writing.set()
        assert attempted.wait(timeout=5)
        # With a row lock the credit waits for recomputation to commit; without
        # it the credit commits here and the following absolute write loses it.
        credited.wait(timeout=0.2)

    original_save = UserProfile.save
    original_update = QuerySet.update

    def delayed_save(instance: UserProfile, *args: object, **kwargs: object) -> None:
        if instance.pk == profile.pk:
            pause_before_absolute_write()
        original_save(instance, *args, **kwargs)

    def delayed_update(queryset: QuerySet, **kwargs: object) -> int:
        if queryset.model is Clan and isinstance(kwargs.get("total_points"), int):
            pause_before_absolute_write()
        return original_update(queryset, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(credit)
        if projection == "profile":
            with patch.object(UserProfile, "save", delayed_save):
                refresh_profile_totals({profile.user_id: profile}, {}, shared_dev=False)
        else:
            with patch.object(QuerySet, "update", delayed_update):
                refresh_clan_totals([clan], [], {}, shared_dev=False)
        future.result(timeout=5)

    target.refresh_from_db()
    assert target.total_points == before + 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("key", ["upcoming", "finished", "private"])
def test_additional_demo_campaign_accepts_legacy_marker_preserving_edits(
    key: str,
) -> None:
    call_command("bootstrap_dev", as_of="2030-01-15T12:00:00+00:00", verbosity=0)
    campaign = Campaign.objects.get(pk=demo_id("campaign", key))
    campaign.description = LEGACY_DEMO_CAMPAIGN_DESCRIPTION + " Retained admin note."
    campaign.start_date += timedelta(days=2)
    campaign.end_date += timedelta(days=3)
    campaign.status = Campaign.Status.PROMOTION
    campaign.save(update_fields=["description", "start_date", "end_date", "status"])
    before = (
        campaign.description,
        campaign.start_date,
        campaign.end_date,
        campaign.status,
    )
    counts = (
        Campaign.objects.count(),
        Mission.objects.count(),
        CampaignParticipant.objects.count(),
    )

    for _ in range(2):
        call_command("bootstrap_dev", as_of="2040-01-15T12:00:00+00:00", verbosity=0)
        campaign.refresh_from_db()
        assert (
            campaign.description,
            campaign.start_date,
            campaign.end_date,
            campaign.status,
        ) == before
        assert (
            Campaign.objects.count(),
            Mission.objects.count(),
            CampaignParticipant.objects.count(),
        ) == counts


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("collision", ["description", "owner", "scope", "clan"])
def test_additional_demo_campaign_rejects_unrelated_identity(collision: str) -> None:
    call_command("bootstrap_dev", verbosity=0)
    campaign = Campaign.objects.get(pk=demo_id("campaign", "private"))
    campaign.description = LEGACY_DEMO_CAMPAIGN_DESCRIPTION
    if collision == "description":
        campaign.description = "Unrelated campaign with the same deterministic ID."
    elif collision == "owner":
        campaign.creator = User.objects.exclude(pk=campaign.creator_id).first()
    elif collision == "scope":
        campaign.scope = Campaign.Scope.GLOBAL
        campaign.target_clan = None
    else:
        campaign.target_clan = Clan.objects.get(name="Demo private clan 02")
    campaign.save()
    before = (
        campaign.description,
        campaign.creator_id,
        campaign.scope,
        campaign.target_clan_id,
    )
    counts = (
        Campaign.objects.count(),
        Mission.objects.count(),
        CampaignParticipant.objects.count(),
    )

    with pytest.raises(CommandError, match="campaign identity collision"):
        call_command("bootstrap_dev", verbosity=0)

    campaign.refresh_from_db()
    assert (
        campaign.description,
        campaign.creator_id,
        campaign.scope,
        campaign.target_clan_id,
    ) == before
    assert (
        Campaign.objects.count(),
        Mission.objects.count(),
        CampaignParticipant.objects.count(),
    ) == counts


@pytest.mark.django_db(transaction=True)
def test_demo_seed_preserves_mismatched_legacy_action_snapshot() -> None:
    """An old fixture needs an explicit repair instead of rewriting its history."""
    catalog = load_catalog_file(DEFAULT_CATALOG)
    load_catalog_data(catalog)
    as_of = datetime(2030, 1, 15, 12, tzinfo=UTC)
    seed_demo_data(catalog, as_of)
    log = ActionLog.objects.get(pk=demo_id("action-log", "1"))
    old_action = ActionMaster.objects.get(code="draft-refill-bottle")
    ActionLog.objects.filter(pk=log.pk).update(action=old_action)

    with pytest.raises(CommandError, match="identity collision"), transaction.atomic():
        seed_demo_data(catalog, as_of)

    log.refresh_from_db()
    assert log.action_id == old_action.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_dev", [False, True])
def test_demo_clan_credit_uses_same_lock_order_as_api(shared_dev: bool) -> None:
    """Different users can share clans: always lock institution before private."""
    institutional = Clan.objects.create(
        id=uuid.UUID(int=2), name="Lock institution", type=Clan.ClanType.INSTITUTIONAL
    )
    private = Clan.objects.create(
        id=uuid.UUID(int=1), name="Lock private", type=Clan.ClanType.PRIVATE
    )
    api_locked_institution = Event()
    seed_started = Event()
    seed_locked_private = Event()
    original_update = QuerySet.update

    def api_credit() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                Clan.all_objects.select_for_update().get(pk=institutional.pk)
                api_locked_institution.set()
                assert seed_started.wait(timeout=5)
                seed_locked_private.wait(timeout=0.2)
                Clan.all_objects.select_for_update().get(pk=private.pk)
        finally:
            close_old_connections()

    def notice_private_lock(queryset: QuerySet, **kwargs: object) -> int:
        result = original_update(queryset, **kwargs)
        if queryset.model is Clan and list(queryset.values_list("pk", flat=True)) == [
            private.pk
        ]:
            seed_locked_private.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(api_credit)
        assert api_locked_institution.wait(timeout=5)
        seed_started.set()
        with patch.object(QuerySet, "update", notice_private_lock):
            refresh_clan_totals(
                [institutional],
                [private],
                {institutional.pk: 7, private.pk: 7},
                shared_dev,
            )
        future.result(timeout=5)


@pytest.mark.django_db(transaction=True)
def test_shared_dev_seed_supports_catalog_without_canonical_careers() -> None:
    """Dev-only placeholder clans must not require an official career mapping."""
    candidate = Path(__file__).resolve().parents[2] / "docs/catalog-candidate-v1.json"
    catalog = load_catalog_file(candidate)
    load_catalog_data(catalog)
    as_of = datetime(2030, 1, 15, 12, tzinfo=UTC)

    first = seed_demo_data(catalog, as_of, shared_dev=True)
    second = seed_demo_data(catalog, as_of, shared_dev=True)

    assert first.user_count == 20
    assert first.created_logs == 24
    assert second.user_created == 0
    assert second.created_logs == 0
    assert Clan.objects.filter(type=Clan.ClanType.INSTITUTIONAL).count() == 3
    assert all(
        clan.name.startswith("Demo institutional clan ")
        for clan in Clan.objects.filter(type=Clan.ClanType.INSTITUTIONAL)
    )
    assert UserProfile.objects.filter(institutional_clan__isnull=False).count() == 20


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_dev", [False, True])
def test_demo_seed_refuses_to_revive_a_dissolved_clan(shared_dev: bool) -> None:
    """A rerun must preserve soft-deleted demo clans and existing activity."""
    candidate = Path(__file__).resolve().parents[2] / "docs/catalog-candidate-v1.json"
    catalog = load_catalog_file(candidate if shared_dev else DEFAULT_CATALOG)
    load_catalog_data(catalog)
    as_of = datetime(2030, 1, 15, 12, tzinfo=UTC)
    seed_demo_data(catalog, as_of, shared_dev=shared_dev)
    clan = Clan.objects.get(name="Demo private clan 01")
    Clan.all_objects.filter(pk=clan.pk).update(deleted_at=timezone.now())
    before = (User.objects.count(), ActionLog.objects.count(), Clan.all_objects.count())

    with pytest.raises(CommandError, match="identity collision"):
        seed_demo_data(catalog, as_of, shared_dev=shared_dev)

    assert (
        User.objects.count(),
        ActionLog.objects.count(),
        Clan.all_objects.count(),
    ) == before
    assert Clan.all_objects.get(pk=clan.pk).deleted_at is not None


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
@pytest.mark.parametrize("collision", ["stable-id", "name-only"])
def test_draft_catalog_rejects_soft_deleted_clan_collisions(collision: str) -> None:
    """Hidden historical clans must not be revived or reused by a local seed."""
    clan_id = (
        stable_reference_id("institutional-clan", "draft-engineering")
        if collision == "stable-id"
        else uuid.uuid4()
    )
    Clan.all_objects.create(
        id=clan_id,
        name="Draft Engineering",
        type=Clan.ClanType.INSTITUTIONAL,
        privacy=Clan.Privacy.PUBLIC,
        deleted_at=timezone.now(),
    )

    with pytest.raises(CommandError, match="identity collision|name collision"):
        call_command("load_catalog", verbosity=0)
    assert ActionCategory.objects.count() == 0
    assert ActionMaster.objects.count() == 0
    assert Clan.all_objects.count() == 1


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
    assert Campaign.objects.count() == 4
    assert set(Campaign.objects.values_list("status", flat=True)) == {
        Campaign.Status.PROMOTION,
        Campaign.Status.IN_PROGRESS,
        Campaign.Status.FINISHED,
    }
    assert (
        Campaign.objects.filter(
            scope=Campaign.Scope.PRIVATE, target_clan__isnull=False
        ).count()
        == 1
    )
    finished_campaign = Campaign.objects.get(status=Campaign.Status.FINISHED)
    assert CampaignParticipant.objects.filter(campaign=finished_campaign).count() == 8
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
    assert str(settings.DATABASES["default"]["HOST"]).lower() in LOCAL_DATABASE_HOSTS
    monkeypatch.setattr("green_iteso.core.database.client_tls_state", lambda _raw: True)
    if command_name == "bootstrap_dev":
        monkeypatch.setenv("DJANGO_ENV", "dev")
        monkeypatch.setattr(settings, "DEPLOYED", False)

    with pytest.raises(
        CommandError, match="requires an unencrypted local PostgreSQL connection"
    ):
        call_command(command_name, verbosity=0)
    assert ActionCategory.objects.count() == 0


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
def test_bootstrap_rejects_soft_deleted_clan_name_collision() -> None:
    unrelated = Clan.objects.create(
        name="Demo private clan 01", type=Clan.ClanType.PRIVATE
    )
    Clan.all_objects.filter(pk=unrelated.pk).update(deleted_at=timezone.now())
    with pytest.raises(CommandError, match="identity collision"):
        call_command("bootstrap_dev", verbosity=0)
    assert Clan.all_objects.filter(name="Demo private clan 01").count() == 1
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
@pytest.mark.parametrize("spent", [0, 2])
def test_local_bootstrap_reconciles_rejected_log_preserving_spending(spent: int) -> None:
    call_command("bootstrap_dev", verbosity=0)
    log = ActionLog.objects.get(pk=demo_id("action-log", "0"))
    profile = UserProfile.objects.get(user_id=log.user_id)
    ActionLog.objects.create(
        user_id=log.user_id,
        action_id=log.action_id,
        institutional_clan_id=log.institutional_clan_id,
        idempotency_key="extra-earned-points",
        points_awarded=7,
        status=ActionLog.Status.APPROVED,
    )
    UserProfile.objects.filter(pk=profile.pk).update(
        total_points=F("total_points") + 7,
        available_points=F("available_points") + 7 - spent,
    )
    ActionLog.objects.filter(pk=log.pk).update(status=ActionLog.Status.REJECTED)

    for _ in range(2):
        call_command("bootstrap_dev", verbosity=0)
        profile.refresh_from_db()
        assert profile.total_points == 7
        assert profile.available_points == 7 - spent


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("invalid_balance", [False, True])
def test_local_bootstrap_rolls_back_unreconcilable_balance(invalid_balance: bool) -> None:
    call_command("bootstrap_dev", verbosity=0)
    log = ActionLog.objects.get(pk=demo_id("action-log", "3"))
    profile = UserProfile.objects.get(user_id=log.user_id)
    if invalid_balance:
        UserProfile.objects.filter(pk=profile.pk).update(
            available_points=profile.total_points + 1
        )
    else:
        UserProfile.objects.filter(pk=profile.pk).update(available_points=0)
        ActionLog.objects.filter(pk=log.pk).update(status=ActionLog.Status.REJECTED)
    # The bootstrap recreates this earlier user's log before hitting the bad
    # balance. Its insertion and every projection update must roll back.
    missing_id = demo_id("action-log", "1")
    ActionLog.objects.filter(pk=missing_id).delete()
    before = list(UserProfile.objects.order_by("pk").values_list(
        "pk", "total_points", "available_points"
    ))
    before_logs = ActionLog.objects.count()

    with pytest.raises(CommandError, match="balance.*docs/semillas-locales"):
        call_command("bootstrap_dev", verbosity=0)

    assert not ActionLog.objects.filter(pk=missing_id).exists()
    assert ActionLog.objects.count() == before_logs
    assert list(UserProfile.objects.order_by("pk").values_list(
        "pk", "total_points", "available_points"
    )) == before


def test_real_approved_catalog_path_stays_closed_pending_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert release_catalog.APPROVED_CATALOG == DEFAULT_CATALOG.with_name(
        "catalog_approved_v1.json"
    )
    assert not release_catalog.APPROVED_CATALOG.exists()
    monkeypatch.setenv("NEON_DEV_APPROVED_CATALOG_SHA256", "a" * 64)
    with pytest.raises(CommandError, match="Could not read canonical approved catalog"):
        release_catalog.load_approved_catalog("dev")


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
    second = call_command("release_catalog", confirm_target="dev", verbosity=0)
    assert "0 created, 2 unchanged" in second
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


def test_approved_catalog_requires_explicit_clan_details() -> None:
    """An approved institutional clan cannot inherit local draft placeholders."""
    payload = {
        "schema_version": 1,
        "status": "APPROVED",
        "approval": {
            "approved_by": "Synthetic test only",
            "reference": "test-payload-not-product-approval",
            "approved_at": "2026-01-01T00:00:00Z",
        },
        "categories": [],
        "actions": [],
        "institutional_clans": [
            {
                "key": "test-career",
                "name": "Test career",
                "type": "INSTITUTIONAL",
                "privacy": "PUBLIC",
            }
        ],
    }
    with pytest.raises(CommandError, match="description"):
        load_catalog_content(
            json.dumps(payload), source="test", expected_status="APPROVED"
        )
    payload["institutional_clans"][0]["description"] = "Test description"
    with pytest.raises(CommandError, match="career"):
        load_catalog_content(
            json.dumps(payload), source="test", expected_status="APPROVED"
        )


@pytest.mark.django_db(transaction=True)
def test_release_catalog_rejects_soft_deleted_clan_identity() -> None:
    """A hidden soft-deleted row is still an identity collision, not a new clan."""
    clan_key = "test-career"
    clan_id = stable_reference_id("institutional-clan", clan_key)
    Clan.all_objects.create(
        id=clan_id,
        name="Test career",
        description="Test description Career key: test-career",
        type=Clan.ClanType.INSTITUTIONAL,
        privacy=Clan.Privacy.PUBLIC,
        deleted_at=timezone.now(),
    )
    payload = {
        "schema_version": 1,
        "status": "APPROVED",
        "approval": {
            "approved_by": "Synthetic test only",
            "reference": "test-payload-not-product-approval",
            "approved_at": "2026-01-01T00:00:00Z",
        },
        "categories": [],
        "actions": [],
        "institutional_clans": [
            {
                "key": clan_key,
                "name": "Test career",
                "description": "Test description",
                "career": "test-career",
                "type": "INSTITUTIONAL",
                "privacy": "PUBLIC",
            }
        ],
    }
    catalog = load_catalog_content(
        json.dumps(payload), source="test", expected_status="APPROVED"
    )
    with pytest.raises(CommandError, match="differs"):
        release_catalog.reject_existing_drift(catalog)


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
    monkeypatch.setenv("DJANGO_ENV", environment)
    monkeypatch.setattr(settings, "DEPLOYED", False)
    with pytest.raises(CommandError, match="DJANGO_DEPLOYED"):
        release_catalog.ensure_release_target(environment)
    monkeypatch.setattr(settings, "DEPLOYED", True)
    monkeypatch.setattr(settings, "CONNECTION_ROLE", "direct")
    with pytest.raises(CommandError, match="pooled app"):
        release_catalog.ensure_release_target(environment)
