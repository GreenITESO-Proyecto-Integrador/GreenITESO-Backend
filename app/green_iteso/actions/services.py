"""Write operations for the actions and virtual exchangeables domain."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from green_iteso.accounts.models import Clan, UserProfile
from green_iteso.accounts.services import ensure_profile
from green_iteso.campaigns.models import UserMissionProgress
from green_iteso.campaigns.services import (
    apply_action_log_to_missions,
    revert_action_log_from_missions,
)

from .models import ActionLog, ExchangeableItem

if TYPE_CHECKING:
    from green_iteso.accounts.models import User


class InsufficientPointsError(ValidationError):
    """Raised when user does not have enough available_points to acquire an item."""

    def __init__(
        self, message: str = "Puntos disponibles insuficientes para este canjeable."
    ) -> None:
        super().__init__(message)


class AlreadyUnlockedError(ValidationError):
    """Raised when a user already owns the virtual item."""

    def __init__(self, message: str = "Ya posees este cosmético en tu perfil.") -> None:
        super().__init__(message)


class ItemInactiveError(ValidationError):
    """Raised when an exchangeable item is inactive or not found."""

    def __init__(self, message: str = "Artículo canjeable no disponible.") -> None:
        super().__init__(message)


@transaction.atomic
def redeem_exchangeable(
    *,
    user: User,
    item_key: str | None = None,
    item_id: uuid.UUID | str | None = None,
) -> UserProfile:
    """Redeem a virtual cosmetic item for the user.

    Stores the unlocked cosmetic key directly in UserProfile.unlocked_cosmetics.
    Deducts spendable available_points atomically while total_points remains untouched.
    Guards against race conditions using select_for_update on UserProfile.
    """
    ensure_profile(user)
    profile = UserProfile.objects.select_for_update().get(user=user)

    if not item_key and not item_id:
        raise ItemInactiveError("Debes proporcionar el ID o key del artículo.")

    try:
        if item_id:
            item = ExchangeableItem.objects.get(id=item_id)
        else:
            item = ExchangeableItem.objects.get(key=item_key)
    except (ExchangeableItem.DoesNotExist, ValueError) as exc:
        raise ItemInactiveError("Artículo canjeable no encontrado.") from exc

    if not item.is_active:
        raise ItemInactiveError("Artículo canjeable no disponible.")

    unlocked_list = profile.unlocked_cosmetics or []
    if item.key in unlocked_list:
        raise AlreadyUnlockedError("Ya posees este cosmético en tu perfil.")

    if profile.available_points < item.points_cost:
        raise InsufficientPointsError(
            "Puntos disponibles insuficientes para realizar este canje."
        )

    # Deduct spendable points ONLY. Historical total_points is strictly immutable.
    profile.available_points -= item.points_cost
    profile.unlocked_cosmetics = [*unlocked_list, item.key]
    profile.save(update_fields=["available_points", "unlocked_cosmetics"])

    return profile


class PointsAlreadySpentError(Exception):
    """The user no longer has enough available points to revoke an award."""


def notify_mission_progress(action_log: ActionLog) -> list[UserMissionProgress]:
    """Tell the missions system that an approved action may advance missions.

    The campaigns domain only skips REJECTED logs, so pending logs are
    filtered here: missions advance at the same point points are credited.

    Args:
        action_log: The action log whose approval should be propagated.

    Returns:
        The mission progress rows the campaigns domain recalculated.
    """
    if action_log.status != ActionLog.Status.APPROVED:
        return []
    return apply_action_log_to_missions(action_log)


def revert_mission_progress(action_log: ActionLog) -> list[UserMissionProgress]:
    """Recalculate the missions a rejected log had contributed to.

    The campaigns domain requires the REJECTED status to be saved first; its
    contribution rows are kept as history and simply stop counting.

    Args:
        action_log: The log whose rejection was already saved.

    Returns:
        The mission progress rows the campaigns domain recalculated.
    """
    if action_log.status != ActionLog.Status.REJECTED:
        return []
    return revert_action_log_from_missions(action_log)


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
