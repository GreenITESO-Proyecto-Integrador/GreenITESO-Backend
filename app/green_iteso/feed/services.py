"""Write operations other domains use to publish into the feed.

Equipo: Equipo 3 - Campañas, Feed y Dashboards
Última modificación: 2026-09-25
"""

from __future__ import annotations

from green_iteso.accounts.models import User

from .images import schedule_feed_image_cleanup
from .models import Post, PostType


def create_shared_evidence_post(
    *, author: User, content: str, image_url: str = ""
) -> Post:
    """Publish a SHARED_EVIDENCE post on behalf of a user (FR-COMM-01).

    This is the entry point other apps call instead of writing Post rows
    directly, so feed rules stay owned by Equipo 3.

    Args:
        author: User the post is attributed to.
        content: Post body; the model rejects blank content.
        image_url: Absolute URL of the evidence image, when one is available.

    Returns:
        The persisted post.
    """
    return Post.objects.create(
        author=author,
        post_type=PostType.SHARED_EVIDENCE,
        content=content,
        image_url=image_url,
    )


def delete_posts_by_author(*, author: User) -> None:
    """Delete every post by ``author`` and, once committed, their uploaded images.

    Call inside the caller's transaction: the images are removed only if it
    commits, and only when no other post still references them.

    Args:
        author: User whose posts are deleted.
    """
    image_urls = list(
        Post.objects.filter(author=author)
        .exclude(image_url="")
        .values_list("image_url", flat=True)
    )
    Post.objects.filter(author=author).delete()
    schedule_feed_image_cleanup(image_urls, author.pk)
