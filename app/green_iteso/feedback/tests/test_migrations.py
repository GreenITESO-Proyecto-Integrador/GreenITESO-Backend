"""Upgrade and rollback of feedback 0001 over an existing database."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

TABLE = "feedback_submission"


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        return name in connection.introspection.table_names(cursor)


@pytest.mark.django_db(transaction=True)
def test_feedback_0001_upgrades_an_existing_database_and_rolls_back() -> None:
    old_target = [("feedback", None)]
    new_target = [("feedback", "0001_initial")]

    executor = MigrationExecutor(connection)
    executor.migrate(old_target)
    try:
        assert not _table_exists(TABLE)
        old_apps = executor.loader.project_state(
            [n for n in executor.loader.graph.leaf_nodes() if n[0] != "feedback"]
        ).apps
        old_user_model = old_apps.get_model("accounts", "User")
        user = old_user_model.objects.create(email="previa@iteso.mx")

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(new_target)
        assert _table_exists(TABLE)
        new_apps = forward_executor.loader.project_state(new_target).apps
        assert (
            new_apps.get_model("accounts", "User")
            .objects.filter(pk=user.pk, email="previa@iteso.mx")
            .exists()
        )
        submission_model = new_apps.get_model("feedback", "FeedbackSubmission")
        submission = submission_model.objects.create(
            user_id=user.pk, subject="Asunto", message="Mensaje de prueba"
        )
        assert submission.status == "OPEN"
        assert submission.category == "OTHER"
        with pytest.raises(IntegrityError), transaction.atomic():
            submission_model.objects.create(
                user_id=user.pk, subject="x", message="y" * 10, rating=6
            )

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        assert not _table_exists(TABLE)
        assert old_user_model.objects.filter(pk=user.pk).exists()
    finally:
        cleanup_executor = MigrationExecutor(connection)
        cleanup_executor.migrate(cleanup_executor.loader.graph.leaf_nodes())
