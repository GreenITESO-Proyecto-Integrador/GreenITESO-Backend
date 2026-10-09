"""Read-only queries for the accounts domain."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, NamedTuple

from django.db.models import Count, DateField, QuerySet, Sum
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

from green_iteso.actions.models import ActionLog
from green_iteso.campaigns.models import Campaign
from green_iteso.gamification.models import UserBadge

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


def _approved_action_logs(user: User) -> QuerySet[ActionLog]:
    """Return ``user``'s own approved action logs, the only ones that count as impact."""
    return ActionLog.objects.filter(user=user, status=ActionLog.Status.APPROVED)


def _sum_impact(logs: QuerySet[ActionLog]) -> ImpactMetrics:
    """Sum the frozen impact snapshots of ``logs``, reporting zero when empty."""
    impact = logs.aggregate(
        co2_kg=Sum("co2_kg_factor_snapshot"),
        water_liters=Sum("water_liters_factor_snapshot"),
        plastic_kg=Sum("plastic_kg_factor_snapshot"),
    )
    return ImpactMetrics(
        co2_kg=impact["co2_kg"] or Decimal("0"),
        water_liters=impact["water_liters"] or Decimal("0"),
        plastic_kg=impact["plastic_kg"] or Decimal("0"),
    )


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

    metrics = _sum_impact(_approved_action_logs(user))

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


MetricsGranularity = Literal["week", "month"]

_PERIOD_TRUNCATORS = {"week": TruncWeek, "month": TruncMonth}


@dataclass(frozen=True)
class CategoryActivity:
    """Approved actions and points the caller earned in one action category."""

    code: str
    name: str
    approved_actions: int
    points: int


@dataclass(frozen=True)
class PeriodActivity:
    """Approved actions and points the caller earned in one week or month."""

    period_start: date
    approved_actions: int
    points: int


@dataclass(frozen=True)
class MetricsFilters:
    """Optional bounds of GET /profile/me/metrics/ (#126).

    ``date_from``/``date_to`` are inclusive local calendar days and narrow
    every metric except the points balance, which is always the current one.
    ``category`` narrows only the action-derived metrics (impact, actions and
    activity); badges and campaigns have no action category.
    """

    date_from: date | None = None
    date_to: date | None = None
    category: str | None = None
    granularity: MetricsGranularity = "week"


@dataclass(frozen=True)
class ActionMetrics:
    """Approved actions and the points they earned, overall and per category."""

    approved: int
    points_earned: int
    by_category: list[CategoryActivity]


@dataclass(frozen=True)
class PointsBalance:
    """The caller's current points, unaffected by the metrics filters."""

    total: int
    available: int


@dataclass(frozen=True)
class AchievementMetrics:
    """Badges earned and finished campaigns the caller took part in."""

    badges_earned: int
    finished_campaigns: int


@dataclass(frozen=True)
class ProfileMetrics:
    """Metrics shown on the caller's ecological profile (#126)."""

    filters: MetricsFilters
    impact: ImpactMetrics
    actions: ActionMetrics
    points: PointsBalance
    activity: list[PeriodActivity]
    achievements: AchievementMetrics


def get_profile_metrics(user: User, filters: MetricsFilters) -> ProfileMetrics:
    """Aggregate ``user``'s own ecological-profile metrics (#126).

    Date bounds are inclusive local calendar days: ``__date`` and the
    week/month truncation both use the active time zone
    (America/Mexico_City), so an action logged late at night counts on the
    day the user saw it. Weeks start on Monday, matching ``TruncWeek``.

    Args:
        user: Account whose own metrics are read.
        filters: Date range, action category and activity bucket size.

    Returns:
        The metrics; empty aggregates are reported as zero, and ``activity``
        lists only periods with at least one approved action, oldest first.
    """
    profile = ensure_profile(user)

    logs = _approved_action_logs(user)
    badges = UserBadge.objects.filter(user=user)
    campaigns = Campaign.objects.filter(
        participants__user=user, status=Campaign.Status.FINISHED
    )
    if filters.date_from is not None:
        logs = logs.filter(created_at__date__gte=filters.date_from)
        badges = badges.filter(earned_at__date__gte=filters.date_from)
        campaigns = campaigns.filter(end_date__date__gte=filters.date_from)
    if filters.date_to is not None:
        logs = logs.filter(created_at__date__lte=filters.date_to)
        badges = badges.filter(earned_at__date__lte=filters.date_to)
        campaigns = campaigns.filter(end_date__date__lte=filters.date_to)
    if filters.category is not None:
        logs = logs.filter(action__category__code=filters.category)

    totals = logs.aggregate(approved=Count("id"), points=Sum("points_awarded"))
    by_category = [
        CategoryActivity(
            code=row["action__category__code"],
            name=row["action__category__name"],
            approved_actions=row["approved_actions"],
            points=row["points"],
        )
        for row in logs.values("action__category__code", "action__category__name")
        .annotate(approved_actions=Count("id"), points=Sum("points_awarded"))
        .order_by("-approved_actions", "action__category__code")
    ]
    truncate = _PERIOD_TRUNCATORS[filters.granularity]
    activity = [
        PeriodActivity(
            period_start=row["period_start"],
            approved_actions=row["approved_actions"],
            points=row["points"],
        )
        for row in logs.annotate(
            period_start=truncate("created_at", output_field=DateField())
        )
        .values("period_start")
        .annotate(approved_actions=Count("id"), points=Sum("points_awarded"))
        .order_by("period_start")
    ]

    return ProfileMetrics(
        filters=filters,
        impact=_sum_impact(logs),
        actions=ActionMetrics(
            approved=totals["approved"],
            points_earned=totals["points"] or 0,
            by_category=by_category,
        ),
        points=PointsBalance(
            total=profile.total_points, available=profile.available_points
        ),
        activity=activity,
        achievements=AchievementMetrics(
            badges_earned=badges.count(),
            finished_campaigns=campaigns.distinct().count(),
        ),
    )


IMPACT_TREND_WEEKS = 4


@dataclass(frozen=True)
class ImpactTrendPoint:
    """One week's worth of the caller's own approved-action impact totals."""

    week_start: date
    co2_kg: Decimal
    water_liters: Decimal
    plastic_kg: Decimal


def get_impact_trend(
    user: User, *, weeks: int = IMPACT_TREND_WEEKS
) -> list[ImpactTrendPoint]:
    """Return ``user``'s own weekly impact totals for the last ``weeks`` ISO weeks.

    Weeks are Monday-start (``TruncWeek``'s default) and the most recent one
    is always the current, possibly partial, week. A week with no approved
    actions still appears, summed to zero -- a trend chart needs one point
    per week regardless of activity to plot a continuous x-axis, the same
    reasoning ``get_ecological_profile`` applies by coalescing a ``None`` sum
    to zero for an all-time total.
    """
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    week_starts = [
        current_week_start - timedelta(weeks=offset)
        for offset in range(weeks - 1, -1, -1)
    ]

    rows = (
        ActionLog.objects.filter(
            user=user,
            status=ActionLog.Status.APPROVED,
            created_at__date__gte=week_starts[0],
        )
        .annotate(week=TruncWeek("created_at"))
        .values("week")
        .annotate(
            co2_kg=Sum("co2_kg_factor_snapshot"),
            water_liters=Sum("water_liters_factor_snapshot"),
            plastic_kg=Sum("plastic_kg_factor_snapshot"),
        )
    )
    totals_by_week = {row["week"].date(): row for row in rows}

    zero = Decimal("0")
    return [
        ImpactTrendPoint(
            week_start=week_start,
            co2_kg=totals_by_week.get(week_start, {}).get("co2_kg") or zero,
            water_liters=totals_by_week.get(week_start, {}).get("water_liters") or zero,
            plastic_kg=totals_by_week.get(week_start, {}).get("plastic_kg") or zero,
        )
        for week_start in week_starts
    ]
