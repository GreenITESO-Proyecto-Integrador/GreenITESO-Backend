"""Coverage for accounts selectors."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.selectors import (
    get_ecological_profile,
    get_impact_trend,
    get_profile_clans,
    get_user_by_id,
)
from green_iteso.actions.models import ActionCategory, ActionLog
from green_iteso.actions.tests.helpers import create_compost_action
from green_iteso.campaigns.models import Campaign, CampaignParticipant


@pytest.mark.django_db
def test_get_user_by_id_returns_matching_account() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    found = get_user_by_id(user.pk)

    assert found == user


@pytest.fixture(name="member")
def member_fixture() -> User:
    """User with an institutional clan on their profile."""
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=institutional)
    return user


@pytest.mark.django_db
def test_get_profile_clans_reads_the_institutional_clan_from_the_profile(
    member: User,
) -> None:
    result = get_profile_clans(member)

    assert result.profile == member.profile
    assert result.institutional_clan == member.profile.institutional_clan
    assert result.active_private_clan is None


@pytest.mark.django_db
def test_get_profile_clans_reads_the_active_private_clan_from_the_membership(
    member: User,
) -> None:
    active = Clan.objects.create(name="Las Ranas", type=Clan.ClanType.PRIVATE)
    inactive = Clan.objects.create(name="Eco Warriors", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=member, clan=active, is_active_private=True)
    ClanMembership.objects.create(user=member, clan=inactive)

    result = get_profile_clans(member)

    assert result.active_private_clan == active
    assert result.institutional_clan == member.profile.institutional_clan


@pytest.mark.django_db
def test_get_profile_clans_ignores_another_users_active_membership(
    member: User,
) -> None:
    other = User.objects.create_user(email="luis@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Las Ranas", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=other, clan=clan, is_active_private=True)

    assert get_profile_clans(member).active_private_clan is None


@pytest.mark.django_db
def test_get_profile_clans_raises_when_the_account_has_no_profile() -> None:
    user = User.objects.create_user(email="sin-perfil@iteso.mx", password="local-only")

    with pytest.raises(UserProfile.DoesNotExist):
        get_profile_clans(user)


def _create_action_log(
    *, user: User, institutional_clan: Clan, status: str, idempotency_key: str
) -> ActionLog:
    category = ActionCategory.objects.create(
        code=f"CAT-{idempotency_key}", name="Waste"
    )
    action = create_compost_action(code=f"ACT-{idempotency_key}", category=category)
    return ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=institutional_clan,
        idempotency_key=idempotency_key,
        points_awarded=action.points,
        co2_kg_factor_snapshot=Decimal("1.500"),
        water_liters_factor_snapshot=Decimal("2.500"),
        plastic_kg_factor_snapshot=Decimal("0.500"),
        status=status,
    )


def _set_created_at(log: ActionLog, when: datetime) -> None:
    """Back-date ``log.created_at`` past ``auto_now_add`` for trend tests."""
    ActionLog.objects.filter(pk=log.pk).update(created_at=when)


def _create_campaign(*, creator: User, status: str, title: str) -> Campaign:
    now = timezone.now()
    return Campaign.objects.create(
        title=title,
        scope=Campaign.Scope.GLOBAL,
        status=status,
        creator=creator,
        start_date=now,
        end_date=now + timedelta(days=1),
    )


@pytest.mark.django_db
def test_get_ecological_profile_returns_placeholders_for_a_fresh_user() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    profile = get_ecological_profile(user)

    assert profile.user == user
    assert profile.level is None
    assert not profile.badges
    assert profile.active_private_clan is None
    assert profile.impact_metrics.co2_kg == Decimal("0")
    assert profile.impact_metrics.water_liters == Decimal("0")
    assert profile.impact_metrics.plastic_kg == Decimal("0")
    assert not profile.finished_campaigns
    # ensure_profile creates the row on first access, matching accounts.services.
    assert UserProfile.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_get_ecological_profile_sums_impact_from_approved_actions_only() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="a1",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="a2",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.REJECTED,
        idempotency_key="a3",
    )
    _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.PENDING_AUDIT,
        idempotency_key="a4",
    )

    profile = get_ecological_profile(user)

    assert profile.impact_metrics.co2_kg == Decimal("3.000")
    assert profile.impact_metrics.water_liters == Decimal("5.000")
    assert profile.impact_metrics.plastic_kg == Decimal("1.000")


@pytest.mark.django_db
def test_get_ecological_profile_includes_only_finished_campaigns() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    finished = _create_campaign(
        creator=user, status=Campaign.Status.FINISHED, title="Semana Verde"
    )
    in_progress = _create_campaign(
        creator=user, status=Campaign.Status.IN_PROGRESS, title="Reto Reciclaje"
    )
    CampaignParticipant.objects.create(campaign=finished, user=user)
    CampaignParticipant.objects.create(campaign=in_progress, user=user)

    profile = get_ecological_profile(user)

    assert [campaign.title for campaign in profile.finished_campaigns] == [
        "Semana Verde"
    ]


@pytest.mark.django_db
def test_get_ecological_profile_includes_the_active_private_clan() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=user, clan=clan, is_active_private=True)

    profile = get_ecological_profile(user)

    assert profile.active_private_clan == clan


def _current_week_start() -> date:
    today = timezone.localdate()
    return today - timedelta(days=today.weekday())


@pytest.mark.django_db
def test_get_impact_trend_returns_four_zero_weeks_for_a_fresh_user() -> None:
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    points = get_impact_trend(user)

    current_week_start = _current_week_start()
    assert [point.week_start for point in points] == [
        current_week_start - timedelta(weeks=3),
        current_week_start - timedelta(weeks=2),
        current_week_start - timedelta(weeks=1),
        current_week_start,
    ]
    assert all(point.co2_kg == Decimal("0") for point in points)
    assert all(point.water_liters == Decimal("0") for point in points)
    assert all(point.plastic_kg == Decimal("0") for point in points)


@pytest.mark.django_db
def test_get_impact_trend_buckets_approved_actions_by_week_and_excludes_others() -> (
    None
):
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    institutional_clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    current_week_start = _current_week_start()
    now = timezone.now()

    # Two approved logs in the current week: sums should add up.
    log_a = _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="current-1",
    )
    _set_created_at(log_a, now)
    log_b = _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="current-2",
    )
    _set_created_at(log_b, now)

    # One approved log two weeks ago: its own, separate bucket.
    log_two_weeks_ago = _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="two-weeks-ago",
    )
    _set_created_at(log_two_weeks_ago, now - timedelta(weeks=2))

    # Rejected in the current week: must not count anywhere.
    log_rejected = _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.REJECTED,
        idempotency_key="rejected",
    )
    _set_created_at(log_rejected, now)

    # Approved but outside the 4-week window: must not count anywhere.
    log_too_old = _create_action_log(
        user=user,
        institutional_clan=institutional_clan,
        status=ActionLog.Status.APPROVED,
        idempotency_key="too-old",
    )
    _set_created_at(log_too_old, now - timedelta(weeks=10))

    points = get_impact_trend(user)
    by_week = {point.week_start: point for point in points}

    current_week_point = by_week[current_week_start]
    assert current_week_point.co2_kg == Decimal("3.000")
    assert current_week_point.water_liters == Decimal("5.000")
    assert current_week_point.plastic_kg == Decimal("1.000")

    two_weeks_ago_point = by_week[current_week_start - timedelta(weeks=2)]
    assert two_weeks_ago_point.co2_kg == Decimal("1.500")
    assert two_weeks_ago_point.water_liters == Decimal("2.500")
    assert two_weeks_ago_point.plastic_kg == Decimal("0.500")

    one_week_ago_point = by_week[current_week_start - timedelta(weeks=1)]
    assert one_week_ago_point.co2_kg == Decimal("0")
    three_weeks_ago_point = by_week[current_week_start - timedelta(weeks=3)]
    assert three_weeks_ago_point.co2_kg == Decimal("0")
