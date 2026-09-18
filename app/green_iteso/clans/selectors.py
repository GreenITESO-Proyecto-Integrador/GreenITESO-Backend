"""Read-only queries for the clans domain."""

from __future__ import annotations

from django.db.models import QuerySet

from green_iteso.accounts.models import Clan


def list_active_clans() -> QuerySet[Clan]:
    """Return non-deleted clans ordered for a stable, paginated directory listing."""
    return Clan.objects.filter(deleted_at__isnull=True).order_by("name")
