"""Upgrade tests CLAUDE.md requires for every PR that changes feed models."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

FEED_BEFORE_LIKES = ("feed", "0002_post_badge_user")
FEED_WITH_LIKES = ("feed", "0003_postlike")


def _targets_with_feed_at(feed_node: tuple[str, str]) -> list[tuple[str, str]]:
    """Every other app at its latest migration, with feed held at ``feed_node``.

    Keeping the other apps at their leaves means the historical ``User`` model
    matches the real ``accounts_user`` table, whatever accounts adds later.
    """
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    return [node for node in leaves if node[0] != "feed"] + [feed_node]


@pytest.mark.django_db(transaction=True)
def test_feed_0003_migration_keeps_posts_and_allows_one_like_per_user() -> None:
    """0003 only adds ``feed_post_likes``.

    Existing posts must survive the upgrade with no likes, and the unique
    constraint must reject a second like from the same user on the same post.
    """
    old_target = _targets_with_feed_at(FEED_BEFORE_LIKES)
    new_target = _targets_with_feed_at(FEED_WITH_LIKES)

    executor = MigrationExecutor(connection)
    executor.migrate(old_target)
    old_apps = executor.loader.project_state(old_target).apps
    ana = old_apps.get_model("accounts", "User").objects.create(email="ana@iteso.mx")
    old_post = old_apps.get_model("feed", "Post").objects.create(
        author_id=ana.pk, post_type="COMMUNITY_MILESTONE", content="Antes de 0003"
    )

    try:
        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(new_target)
        new_apps = forward_executor.loader.project_state(new_target).apps
        post = new_apps.get_model("feed", "Post").objects.get(pk=old_post.pk)
        like_model = new_apps.get_model("feed", "PostLike")

        assert post.content == "Antes de 0003"
        assert not like_model.objects.filter(post_id=post.pk).exists()

        like_model.objects.create(post_id=post.pk, user_id=ana.pk)
        with pytest.raises(IntegrityError), transaction.atomic():
            like_model.objects.create(post_id=post.pk, user_id=ana.pk)
    finally:
        cleanup_executor = MigrationExecutor(connection)
        cleanup_executor.migrate(cleanup_executor.loader.graph.leaf_nodes())
