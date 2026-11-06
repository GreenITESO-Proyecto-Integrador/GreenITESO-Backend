"""Profile frame and background shown next to a person's name and photo.

A profile keeps the cosmetics it picked as keys in ``preferences``
(``active_frame``, ``active_background``). That JSON is free-form and written by
the user, so it is never served to others as is: each key is looked up in the
``ExchangeableItem`` catalog and only the catalog's own ``image_url`` goes out.
A key that is not in the catalog, or belongs to another kind of item, shows
nothing.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from green_iteso.actions.models import ExchangeableItem

from .models import User

FRAME_PREFERENCE = "active_frame"
BACKGROUND_PREFERENCE = "active_background"

_CATALOG_CACHE_KEY = "_cosmetic_catalog"
_COSMETIC_CATEGORIES = (
    ExchangeableItem.Category.FRAME,
    ExchangeableItem.Category.BACKGROUND,
)


def _catalog(context: MutableMapping[str, Any]) -> dict[tuple[str, str], str]:
    """``(category, key) -> image_url``, read once per serialization context.

    The catalog is small, so one query per request beats one per person in a
    list of posts or a roster.
    """
    cached = context.get(_CATALOG_CACHE_KEY)
    if cached is None:
        rows = ExchangeableItem.objects.filter(
            category__in=_COSMETIC_CATEGORIES
        ).values_list("category", "key", "image_url")
        cached = {(category, key): url for category, key, url in rows}
        context[_CATALOG_CACHE_KEY] = cached
    return cached


def _image_for(
    context: MutableMapping[str, Any],
    user: User,
    *,
    preference: str,
    category: str,
) -> str:
    profile = getattr(user, "profile", None)
    preferences = getattr(profile, "preferences", None)
    if not isinstance(preferences, dict):
        return ""
    key = preferences.get(preference)
    if not isinstance(key, str) or not key:
        return ""
    return _catalog(context).get((category, key), "")


def frame_image_url(user: User, context: MutableMapping[str, Any]) -> str:
    """The catalog image of ``user``'s active avatar frame, or "" when there is none."""
    return _image_for(
        context,
        user,
        preference=FRAME_PREFERENCE,
        category=ExchangeableItem.Category.FRAME,
    )


def background_image_url(user: User, context: MutableMapping[str, Any]) -> str:
    """The catalog image of ``user``'s active profile background, or "" when there is none."""
    return _image_for(
        context,
        user,
        preference=BACKGROUND_PREFERENCE,
        category=ExchangeableItem.Category.BACKGROUND,
    )
