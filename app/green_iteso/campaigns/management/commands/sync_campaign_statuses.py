"""Management command that advances campaign statuses by date."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from green_iteso.campaigns.services import sync_campaign_statuses


class Command(BaseCommand):
    help = "Advance campaign statuses (PROMOTION, IN_PROGRESS, FINISHED) by date."

    def handle(self, *args: Any, **options: Any) -> None:
        updated = sync_campaign_statuses()
        self.stdout.write(f"Updated {updated} campaign(s).")
