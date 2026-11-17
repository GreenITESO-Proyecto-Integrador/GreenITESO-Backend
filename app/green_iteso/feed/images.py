"""Storage cleanup for feed post images.

``ImageUploadView`` stores an image under ``feed/<owner_id>/<file>`` and hands
the client a ``feed-image`` URL to put in ``Post.image_url``. Nothing else owns
that file, so it goes away with the last post that references it.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse
from uuid import UUID

from django.db import transaction
from django.urls import Resolver404, resolve

from green_iteso.core.image_storage import delete_image
from green_iteso.feed.models import Post

FEED_IMAGE_PREFIX = "feed"


def _owned_feed_image(image_url: str, author_id: UUID) -> str | None:
    """Return the object key when ``image_url`` is a feed upload by ``author_id``.

    ``image_url`` is free text, so it may point at someone else's upload, an
    action-evidence URL or an external site. Only the author's own feed
    uploads qualify.
    """
    if not image_url:
        return None
    try:
        match = resolve(urlparse(image_url).path)
    except Resolver404:
        return None
    if match.url_name != "feed-image":
        return None
    owner_id = match.kwargs.get("owner_id")
    if str(owner_id) != str(author_id):
        return None
    return f"{FEED_IMAGE_PREFIX}/{owner_id}/{match.kwargs['filename']}"


def _delete_if_unreferenced(author_id: UUID, object_key: str) -> None:
    """Delete ``object_key`` unless a committed post still points at it.

    Matches on the ``uploads/<owner>/<file>`` path, so a different scheme,
    host or query string still counts as a reference.
    """
    filename = object_key.rsplit("/", 1)[1]
    still_used = Post.objects.filter(
        image_url__contains=f"/uploads/{author_id}/{filename}"
    ).exists()
    if not still_used:
        delete_image(FEED_IMAGE_PREFIX, author_id, object_key)


def schedule_feed_image_cleanup(
    image_urls: Iterable[str], author_id: UUID | None
) -> None:
    """Delete the author's feed images no post references any more.

    Call after the posts were changed, inside the same transaction. The
    reference check and the delete run only once it commits, so concurrent
    deletes of posts sharing an image cannot both see the other's post and
    leave the file orphaned.
    """
    if author_id is None:
        return
    for image_url in set(image_urls):
        object_key = _owned_feed_image(image_url, author_id)
        if object_key is None:
            continue
        transaction.on_commit(
            lambda key=object_key: _delete_if_unreferenced(author_id, key)
        )
