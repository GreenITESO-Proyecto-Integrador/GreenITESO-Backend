"""Write operations for the actions and virtual exchangeables domain."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from green_iteso.accounts.models import UserProfile
from green_iteso.accounts.services import ensure_profile

from .models import ExchangeableItem

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
