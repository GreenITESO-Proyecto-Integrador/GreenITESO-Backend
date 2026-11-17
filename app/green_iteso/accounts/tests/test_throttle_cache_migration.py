"""Upgrade and rollback of accounts 0022, the shared auth throttle cache table (#217)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

TABLE = "accounts_auth_throttle_cache"


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        return name in connection.introspection.table_names(cursor)


@pytest.mark.django_db(transaction=True)
def test_accounts_0022_adds_and_removes_the_auth_throttle_cache_table() -> None:
    """Upgrade and rollback of the shared throttle cache table (#217)."""
    old_target = [("accounts", "0021_user_ban_audit")]
    new_target = [("accounts", "0022_auth_throttle_cache")]

    executor = MigrationExecutor(connection)
    executor.migrate(old_target)
    try:
        assert not _table_exists(TABLE)

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(new_target)
        assert _table_exists(TABLE)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (cache_key, value, expires) "
                "VALUES (%s, %s, now())",
                [":1:throttle_auth_login_x", "1"],
            )

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        assert not _table_exists(TABLE)
    finally:
        cleanup_executor = MigrationExecutor(connection)
        cleanup_executor.migrate(cleanup_executor.loader.graph.leaf_nodes())
