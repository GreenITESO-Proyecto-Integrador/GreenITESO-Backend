"""Read-only queries for the accounts domain."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from django.db.models import QuerySet, Sum

from green_iteso.actions.models import ActionLog
from green_iteso.campaigns.models import Campaign

from .models import Clan, ClanMembership, User, UserProfile
from .services import ensure_profile


class ProfileClans(NamedTuple):
    """The profile of a user together with the clans their points credit."""

    profile: UserProfile
    institutional_clan: Clan | None
    active_private_clan: Clan | None


def get_profile_clans(user: User) -> ProfileClans:
    """Return ``user``'s profile and the two clans credited on a points event.

    The institutional clan is a column on UserProfile. The active private clan
    is not: it is the membership flagged ``is_active_private``, which
    ClanMembership restricts to at most one per user, so it is read from there
    instead of being duplicated on the profile.

    Args:
        user: Account whose profile is read.

    Returns:
        The profile plus both clans; either clan is None when unset.

    Raises:
        UserProfile.DoesNotExist: If the account has no profile yet.
    """
    profile = UserProfile.objects.select_related("institutional_clan").get(user=user)
    membership = (
        ClanMembership.objects.select_related("clan")
        .filter(user=user, is_active_private=True)
        .first()
    )
    return ProfileClans(
        profile=profile,
        institutional_clan=profile.institutional_clan,
        active_private_clan=membership.clan if membership else None,
    )


def get_user_by_id(user_id: uuid.UUID) -> User:
    """Return the account identified by ``user_id``."""
    return User.objects.get(pk=user_id)


def list_users() -> QuerySet[User]:
    """Return all accounts, ordered for a stable admin directory listing."""
    return User.objects.order_by("email")


@dataclass(frozen=True)
class ImpactMetrics:
    """Sum of the environmental impact of the caller's own approved actions."""

    co2_kg: Decimal
    water_liters: Decimal
    plastic_kg: Decimal


@dataclass(frozen=True)
class FinishedCampaign:
    """Minimal reference to a campaign the caller participated in and that ended."""

    id: uuid.UUID
    title: str
    end_date: datetime


@dataclass(frozen=True)
class EcologicalProfile:
    """Aggregated view composing the caller's own data with E1/E3 data (T2-20)."""

    user: User
    profile: UserProfile
    active_private_clan: Clan | None
    level: int | None
    badges: list[object]
    impact_metrics: ImpactMetrics
    finished_campaigns: list[FinishedCampaign]


def get_ecological_profile(user: User) -> EcologicalProfile:
    """Aggregate ``user``'s own profile with E1 (points/badges) and E3 (campaigns) data.

    E1/E3 data is read directly via the ORM (same process, same database, not
    a network call to a separate service), per T2-20's own open decision:
    "Contrato exacto con Eq1/Eq3: lectura en tiempo real vs datos
    denormalizados" is resolved here as a real-time read.

    ``level`` and ``badges`` are placeholders (``None`` / ``[]``): E1 hasn't
    built a leveling system or the ``Badge``/``UserBadge`` models yet, so
    there is nothing to query. This is exactly the "external service doesn't
    respond" case T2-20's AC2 anticipates ("degrada con placeholders sin
    romper") -- there's no data to fail to fetch, so degrading to an explicit
    placeholder is the correct behavior today, not a workaround.
    """
    profile = ensure_profile(user)
    active_membership = (
        ClanMembership.objects.filter(user=user, is_active_private=True)
        .select_related("clan")
        .first()
    )
    active_private_clan = active_membership.clan if active_membership else None

    impact = ActionLog.objects.filter(
        user=user, status=ActionLog.Status.APPROVED
    ).aggregate(
        co2_kg=Sum("co2_kg_factor_snapshot"),
        water_liters=Sum("water_liters_factor_snapshot"),
        plastic_kg=Sum("plastic_kg_factor_snapshot"),
    )
    metrics = ImpactMetrics(
        co2_kg=impact["co2_kg"] or Decimal("0"),
        water_liters=impact["water_liters"] or Decimal("0"),
        plastic_kg=impact["plastic_kg"] or Decimal("0"),
    )

    finished_campaigns = [
        FinishedCampaign(
            id=campaign.id, title=campaign.title, end_date=campaign.end_date
        )
        for campaign in Campaign.objects.filter(
            participants__user=user, status=Campaign.Status.FINISHED
        ).order_by("-end_date")
    ]

    return EcologicalProfile(
        user=user,
        profile=profile,
        active_private_clan=active_private_clan,
        level=None,
        badges=[],
        impact_metrics=metrics,
        finished_campaigns=finished_campaigns,
    )
