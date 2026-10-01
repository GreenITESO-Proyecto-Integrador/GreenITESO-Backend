"""DRF selectors for the actions domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from .models import ActionCategory, ActionMaster, Reward, RewardRedemption

if TYPE_CHECKING:
    from green_iteso.accounts.models import User


def list_active_action_categories() -> QuerySet[ActionCategory]:
    """Return action categories ordered by code."""
    return ActionCategory.objects.all().order_by("code")


def list_active_actions() -> QuerySet[ActionMaster]:
    """Return active actions ordered by code."""
    return ActionMaster.objects.filter(is_active=True).order_by("code")


def list_active_rewards() -> QuerySet[Reward]:
    """Return active rewards ordered by points_cost and name."""
    return Reward.objects.filter(is_active=True).order_by("points_cost", "name")


def list_user_redemptions(user: User) -> QuerySet[RewardRedemption]:
    """Return redemptions for a user ordered by newest first."""
    return (
        RewardRedemption.objects.filter(user=user)
        .select_related("reward")
        .order_by("-created_at")
    )
