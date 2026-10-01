"""Write operations for the actions and rewards domain."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from green_iteso.accounts.models import UserProfile
from green_iteso.accounts.services import ensure_profile

from .models import Reward, RewardRedemption

if TYPE_CHECKING:
    from green_iteso.accounts.models import User


class InsufficientPointsError(ValidationError):
    """Raised when user does not have enough available points to redeem a reward."""

    def __init__(
        self, message: str = "Puntos disponibles insuficientes para realizar el canje."
    ) -> None:
        super().__init__(message)


class RewardOutOfStockError(ValidationError):
    """Raised when a reward has no stock available."""

    def __init__(self, message: str = "Recompensa agotada.") -> None:
        super().__init__(message)


class RewardInactiveError(ValidationError):
    """Raised when a reward is inactive or not found."""

    def __init__(self, message: str = "Recompensa no disponible.") -> None:
        super().__init__(message)


@transaction.atomic
def redeem_reward(*, user: User, reward_id: uuid.UUID | str) -> RewardRedemption:
    """Redeem a reward for the user, deducting available points and stock atomically.

    Locks both the user's UserProfile and the Reward with select_for_update to prevent
    race conditions and double-spending. Available points are deducted while
    historical total_points remains untouched.
    """
    ensure_profile(user)
    profile = UserProfile.objects.select_for_update().get(user=user)

    try:
        reward = Reward.objects.select_for_update().get(id=reward_id)
    except Reward.DoesNotExist as exc:
        raise RewardInactiveError("Recompensa no encontrada.") from exc

    if not reward.is_active:
        raise RewardInactiveError("Recompensa no disponible.")

    if reward.stock <= 0:
        raise RewardOutOfStockError("Recompensa agotada.")

    if profile.available_points < reward.points_cost:
        raise InsufficientPointsError(
            "Puntos disponibles insuficientes para realizar el canje."
        )

    reward.stock -= 1
    reward.save(update_fields=["stock", "updated_at"])

    profile.available_points -= reward.points_cost
    profile.save(update_fields=["available_points"])

    redemption = RewardRedemption.objects.create(
        user=user,
        reward=reward,
        points_spent=reward.points_cost,
        status=RewardRedemption.Status.COMPLETED,
    )

    return redemption
