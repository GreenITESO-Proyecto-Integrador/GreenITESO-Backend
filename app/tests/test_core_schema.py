"""Focused PostgreSQL checks for the provisional T9a model contract."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.campaigns.models import Campaign, Mission


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
        ("actions", "0004_align_approved_validation_type"),
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
