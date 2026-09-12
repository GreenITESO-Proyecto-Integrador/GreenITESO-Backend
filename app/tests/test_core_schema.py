"""Focused PostgreSQL checks for the provisional T9a model contract."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
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

DOMAIN_TABLES = {
    User: "accounts_user",
    Clan: "accounts_clan",
    UserProfile: "accounts_user_profile",
    ClanMembership: "accounts_clan_membership",
    ActionCategory: "actions_action_category",
    ActionMaster: "actions_action_master",
    ActionLog: "actions_action_log",
    ActionLogMissionContribution: "actions_action_log_mission_contribution",
    Campaign: "campaigns_campaign",
    Mission: "campaigns_mission",
    CampaignParticipant: "campaigns_campaign_participant",
    UserMissionProgress: "campaigns_user_mission_progress",
}

RENAMED_TABLES = {
    "accounts_userprofile": "accounts_user_profile",
    "accounts_clanmembership": "accounts_clan_membership",
    "actions_actioncategory": "actions_action_category",
    "actions_actionmaster": "actions_action_master",
    "actions_actionlog": "actions_action_log",
    "actions_actionlogmissioncontribution": "actions_action_log_mission_contribution",
    "campaigns_campaignparticipant": "campaigns_campaign_participant",
    "campaigns_usermissionprogress": "campaigns_user_mission_progress",
}


@pytest.mark.django_db
def test_custom_user_manager_and_admin_flags() -> None:
    user = User.objects.create_user(
        email="student@iteso.mx", password="safe-local-password"
    )
    assert user.check_password("safe-local-password")
    assert user.username is None
    assert not user.is_staff

    admin = User.objects.create_superuser(
        email="admin@iteso.mx", password="safe-local-password"
    )
    assert admin.is_staff and admin.is_superuser
    assert admin.role == User.Role.ADMIN


@pytest.mark.django_db(transaction=True)
def test_custom_user_table_replaces_default_auth_user_table() -> None:
    assert User._meta.db_table == "accounts_user"
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.auth_user')")
        assert cursor.fetchone()[0] is None


@pytest.mark.django_db(transaction=True)
def test_domain_models_use_canonical_tables_and_preserve_auth_m2m_names() -> None:
    assert len(DOMAIN_TABLES) == 12
    assert {model._meta.db_table for model in DOMAIN_TABLES} == set(
        DOMAIN_TABLES.values()
    )
    assert {model._meta.db_table: model for model in DOMAIN_TABLES} == {
        table: model for model, table in DOMAIN_TABLES.items()
    }
    assert User._meta.get_field("groups").remote_field.through._meta.db_table == (
        "accounts_user_groups"
    )
    assert (
        User._meta.get_field("user_permissions").remote_field.through._meta.db_table
        == "accounts_user_user_permissions"
    )
    assert Group._meta.db_table == "auth_group"


@pytest.mark.django_db(transaction=True)
def test_domain_table_renames_preserve_rows_fks_m2m_and_oids() -> None:
    """Physical table renames keep data and relationships in both directions."""
    old_target = [
        ("accounts", "0003_alter_user_options_clan_clan_type_valid_and_more"),
        ("actions", "0004_align_approved_validation_type"),
        ("campaigns", "0003_mission_action_master_column"),
    ]
    new_target = [
        ("accounts", "0004_alter_clan_table_alter_clanmembership_table_and_more"),
        ("actions", "0005_alter_actioncategory_table_alter_actionlog_table_and_more"),
        (
            "campaigns",
            "0004_alter_campaign_table_alter_campaignparticipant_table_and_more",
        ),
    ]
    executor = MigrationExecutor(connection)
    executor.migrate(old_target)
    old_apps = executor.loader.project_state(old_target).apps

    old_user_model = old_apps.get_model("accounts", "User")
    old_clan_model = old_apps.get_model("accounts", "Clan")
    old_profile_model = old_apps.get_model("accounts", "UserProfile")
    old_membership_model = old_apps.get_model("accounts", "ClanMembership")
    old_group_model = old_apps.get_model("auth", "Group")
    old_category_model = old_apps.get_model("actions", "ActionCategory")
    old_action_model = old_apps.get_model("actions", "ActionMaster")
    old_log_model = old_apps.get_model("actions", "ActionLog")
    old_contribution_model = old_apps.get_model(
        "actions", "ActionLogMissionContribution"
    )
    old_campaign_model = old_apps.get_model("campaigns", "Campaign")
    old_mission_model = old_apps.get_model("campaigns", "Mission")
    old_participant_model = old_apps.get_model("campaigns", "CampaignParticipant")
    old_progress_model = old_apps.get_model("campaigns", "UserMissionProgress")

    user = old_user_model.objects.create(email="rename@example.test")
    group = old_group_model.objects.create(name="rename-group")
    user.groups.add(group)
    clan = old_clan_model.objects.create(name="Rename clan", type="INSTITUTIONAL")
    old_profile_model.objects.create(user=user, institutional_clan=clan)
    old_membership_model.objects.create(user=user, clan=clan, role="MEMBER")
    category = old_category_model.objects.create(code="rename", name="Rename")
    action = old_action_model.objects.create(
        code="rename-action",
        category=category,
        name="Rename action",
        description="Rename preservation",
        points=1,
        daily_limit=1,
        validation_type="NONE",
    )
    now = timezone.now()
    campaign = old_campaign_model.objects.create(
        title="Rename campaign",
        scope="GLOBAL",
        creator=user,
        start_date=now,
        end_date=now + timedelta(days=1),
    )
    mission = old_mission_model.objects.create(
        campaign=campaign, action=action, target_count=1
    )
    participant = old_participant_model.objects.create(campaign=campaign, user=user)
    progress = old_progress_model.objects.create(
        user=user, mission=mission, current_count=1
    )
    log = old_log_model.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        campaign=campaign,
        idempotency_key="rename-1",
        points_awarded=1,
    )
    contribution = old_contribution_model.objects.create(
        action_log=log, mission=mission
    )
    ids = {
        "user": user.pk,
        "group": group.pk,
        "clan": clan.pk,
        "profile": user.pk,
        "membership": old_membership_model.objects.get(user=user, clan=clan).pk,
        "category": category.pk,
        "action": action.pk,
        "campaign": campaign.pk,
        "mission": mission.pk,
        "participant": participant.pk,
        "progress": progress.pk,
        "log": log.pk,
        "contribution": contribution.pk,
    }

    def table_oids() -> dict[str, int]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, oid::bigint FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace "
                "AND relname = ANY(%s)",
                [list(RENAMED_TABLES) + list(RENAMED_TABLES.values())],
            )
            return dict(cursor.fetchall())

    old_oids = table_oids()
    assert set(RENAMED_TABLES) <= set(old_oids)

    try:
        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(new_target)
        forward_apps = forward_executor.loader.project_state(new_target).apps
        forward_oids = table_oids()
        assert set(RENAMED_TABLES.values()) <= set(forward_oids)
        for old_name, new_name in RENAMED_TABLES.items():
            assert old_name not in forward_oids
            assert forward_oids[new_name] == old_oids[old_name]

        new_user_model = forward_apps.get_model("accounts", "User")
        new_profile_model = forward_apps.get_model("accounts", "UserProfile")
        new_membership_model = forward_apps.get_model("accounts", "ClanMembership")
        new_log_model = forward_apps.get_model("actions", "ActionLog")
        new_contribution_model = forward_apps.get_model(
            "actions", "ActionLogMissionContribution"
        )
        new_participant_model = forward_apps.get_model(
            "campaigns", "CampaignParticipant"
        )
        new_progress_model = forward_apps.get_model("campaigns", "UserMissionProgress")
        assert (
            new_user_model.objects.get(pk=ids["user"])
            .groups.filter(pk=ids["group"])
            .exists()
        )
        assert new_profile_model.objects.get(pk=ids["profile"]).user_id == ids["user"]
        assert (
            new_membership_model.objects.get(pk=ids["membership"]).user_id
            == ids["user"]
        )
        assert new_log_model.objects.get(pk=ids["log"]).campaign_id == ids["campaign"]
        assert (
            new_contribution_model.objects.get(pk=ids["contribution"]).mission_id
            == ids["mission"]
        )
        assert (
            new_participant_model.objects.get(pk=ids["participant"]).user_id
            == ids["user"]
        )
        assert (
            new_progress_model.objects.get(pk=ids["progress"]).mission_id
            == ids["mission"]
        )

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        reverse_oids = table_oids()
        for old_name, new_name in RENAMED_TABLES.items():
            assert reverse_oids[old_name] == old_oids[old_name]
            assert new_name not in reverse_oids
        reverted_apps = reverse_executor.loader.project_state(old_target).apps
        assert (
            reverted_apps.get_model("accounts", "User")
            .objects.get(pk=ids["user"])
            .groups.filter(pk=ids["group"])
            .exists()
        )
        assert (
            reverted_apps.get_model("actions", "ActionLog")
            .objects.get(pk=ids["log"])
            .campaign_id
            == ids["campaign"]
        )

        final_executor = MigrationExecutor(connection)
        final_executor.migrate(new_target)
        final_oids = table_oids()
        for old_name, new_name in RENAMED_TABLES.items():
            assert old_name not in final_oids
            assert final_oids[new_name] == old_oids[old_name]
    finally:
        MigrationExecutor(connection).migrate(new_target)


@pytest.mark.django_db(transaction=True)
def test_approved_erd_field_names_are_exposed() -> None:
    """The ORM reflects the approved validation and mission column names."""
    validation = ActionMaster._meta.get_field("validation_type")
    assert validation.choices == [("NONE", "None"), ("PHOTO", "Photo")]
    assert Mission._meta.get_field("action").db_column == "action_master_id"
    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, "campaigns_mission"
            )
        }
    assert "action_master_id" in columns
    assert "action_id" not in columns


@pytest.mark.django_db
def test_active_private_membership_is_unique_per_user() -> None:
    user = User.objects.create_user(email="member@iteso.mx")
    first = Clan.objects.create(name="First", type=Clan.ClanType.PRIVATE)
    second = Clan.objects.create(name="Second", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=user, clan=first, is_active_private=True)
    with pytest.raises(IntegrityError):
        ClanMembership.objects.create(user=user, clan=second, is_active_private=True)


@pytest.mark.django_db
def test_catalog_and_campaign_checks_are_database_constraints() -> None:
    category = ActionCategory.objects.create(code="mobility", name="Mobility")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ActionMaster.objects.create(
                code="bad-points",
                category=category,
                name="Bad",
                description="invalid",
                points=0,
                daily_limit=1,
                validation_type=ActionMaster.ValidationType.NONE,
            )

    user = User.objects.create_user(email="creator@iteso.mx")
    now = timezone.now()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Campaign.objects.create(
                title="Backwards",
                scope=Campaign.Scope.GLOBAL,
                creator=user,
                start_date=now,
                end_date=now - timedelta(seconds=1),
            )


@pytest.mark.django_db(transaction=True)
def test_raw_sql_delete_is_rejected_at_transaction_commit_and_orm_protects() -> None:
    user = User.objects.create_user(email="actor@iteso.mx")
    profile = UserProfile.objects.create(user=user)
    clan = Clan.objects.create(name="Institutional", type=Clan.ClanType.INSTITUTIONAL)
    category = ActionCategory.objects.create(code="water", name="Water")
    action = ActionMaster.objects.create(
        code="refill",
        category=category,
        name="Refill",
        description="Refill a bottle",
        points=5,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
        water_liters_factor=Decimal("1.000"),
    )
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=clan,
        idempotency_key="attempt-1",
        points_awarded=5,
        water_liters_factor_snapshot=Decimal("1.000"),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                cursor.execute("DELETE FROM accounts_user WHERE id = %s", [user.pk])

    with pytest.raises(IntegrityError):
        user.delete()
    with pytest.raises(IntegrityError):
        clan.delete()
    assert ActionLog.objects.filter(pk=log.pk).exists()
    assert UserProfile.objects.filter(pk=profile.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_legacy_action_validation_value_migrates_to_approved_none_enum() -> None:
    """The approved validation label is preserved when upgrading an old row."""
    executor = MigrationExecutor(connection)
    old_target = [
        ("actions", "0003_actionlog_action_log_status_valid_and_more"),
    ]
    forward_target = [
        ("accounts", "0004_alter_clan_table_alter_clanmembership_table_and_more"),
        ("actions", "0005_alter_actioncategory_table_alter_actionlog_table_and_more"),
        (
            "campaigns",
            "0004_alter_campaign_table_alter_campaignparticipant_table_and_more",
        ),
    ]
    executor.migrate(old_target)
    old_apps = executor.loader.project_state(old_target).apps
    old_category = old_apps.get_model("actions", "ActionCategory").objects.create(
        code="legacy-category", name="Legacy"
    )
    old_action = old_apps.get_model("actions", "ActionMaster").objects.create(
        code="legacy-action",
        category=old_category,
        name="Legacy action",
        description="Value stored by the previous draft",
        points=1,
        daily_limit=1,
        validation_mode="DECLARATIVE_BUTTON",
    )

    try:
        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(forward_target)
        current_apps = forward_executor.loader.project_state(forward_target).apps
        current_action = current_apps.get_model("actions", "ActionMaster").objects.get(
            pk=old_action.pk
        )
        assert current_action.validation_type == "NONE"

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        reverted_apps = reverse_executor.loader.project_state(old_target).apps
        reverted_action = reverted_apps.get_model(
            "actions", "ActionMaster"
        ).objects.get(pk=old_action.pk)
        assert reverted_action.validation_mode == "DECLARATIVE_BUTTON"
    finally:
        MigrationExecutor(connection).migrate(forward_target)
