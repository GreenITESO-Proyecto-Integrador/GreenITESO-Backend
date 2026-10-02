"""Write operations for the actions domain."""

from __future__ import annotations

from django.db import transaction
from django.db.models import F

from green_iteso.accounts.models import Clan, UserProfile

from .models import ActionLog


class PointsAlreadySpentError(Exception):
    """The user no longer has enough available points to revoke an award."""


@transaction.atomic
def revoke_awarded_points(action_log: ActionLog) -> None:
    """Deduct exactly the points an approved log credited when it is rejected.

    Uses the frozen ``points_awarded`` and the clans recorded on the log, not
    the current catalog value or the user's current clans, so the deduction
    mirrors the original credit.

    Args:
        action_log: The approved log being rejected.

    Raises:
        PointsAlreadySpentError: The user's available balance is lower than the
            points to deduct, so revoking would leave it negative.
    """
    points = action_log.points_awarded
    profile = UserProfile.objects.select_for_update().get(user_id=action_log.user_id)
    if profile.available_points < points:
        raise PointsAlreadySpentError

    UserProfile.objects.filter(pk=profile.pk).update(
        total_points=F("total_points") - points,
        available_points=F("available_points") - points,
    )
    clan_ids = [
        clan_id
        for clan_id in (
            action_log.institutional_clan_id,
            action_log.credited_private_clan_id,
        )
        if clan_id is not None
    ]
    # all_objects: a dissolved clan keeps its historical total, so it is
    # debited too.
    # Match credit/seed lock order: institutional clan before private clan.
    # A bulk UPDATE may visit these rows in the opposite database-plan order.
    for clan_id in clan_ids:
        Clan.all_objects.filter(pk=clan_id).update(
            total_points=F("total_points") - points
        )
