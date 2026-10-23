"""Tests for campaign invitation recipients."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant, Mission
from green_iteso.campaigns.notifications import notify_campaign_invite
from green_iteso.campaigns.services import apply_action_log_to_missions
from green_iteso.campaigns.tests.helpers import campaign_data
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import BULK_CREATE_BATCH_SIZE, notify_many


@pytest.mark.django_db
def test_private_campaign_invite_only_reaches_accepted_members(
    user: User, other_user: User, clan: Clan
) -> None:
    pending = User.objects.create_user(email="pending@example.test", password="x")
    rejected = User.objects.create_user(email="rejected@example.test", password="x")
    ClanMembership.objects.create(user=user, clan=clan)
    ClanMembership.objects.create(user=other_user, clan=clan)
    ClanMembership.objects.create(
        user=pending, clan=clan, status=ClanMembership.Status.PENDING
    )
    ClanMembership.objects.create(
        user=rejected, clan=clan, status=ClanMembership.Status.REJECTED
    )
    campaign = Campaign.objects.create(
        **campaign_data(creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan)
    )

    sent = notify_campaign_invite(campaign)

    assert sent == 1
    assert list(Notification.objects.values_list("user_id", flat=True)) == [
        other_user.pk
    ]


@pytest.mark.django_db
def test_campaign_invite_goes_through_notify_many(
    user: User, other_user: User, clan: Clan
) -> None:
    ClanMembership.objects.create(user=user, clan=clan)
    ClanMembership.objects.create(user=other_user, clan=clan)
    campaign = Campaign.objects.create(
        **campaign_data(creator=user, scope=Campaign.Scope.PRIVATE, target_clan=clan)
    )

    with patch(
        "green_iteso.campaigns.notifications.notify_many",
        wraps=notify_many,
    ) as spy:
        notify_campaign_invite(campaign)

    spy.assert_called_once()
    assert spy.call_args.args[1] == Notification.NotificationType.CAMPAIGN_INVITE
    assert spy.call_args.kwargs == {"campaign_name": campaign.title}
    notification = Notification.objects.get(user=other_user)
    assert (
        notification.notification_type == Notification.NotificationType.CAMPAIGN_INVITE
    )
    assert campaign.title in notification.message


@pytest.mark.django_db
def test_completing_a_mission_creates_mission_completed_notification(
    user: User, admin_user: User, action: ActionMaster, campaign_factory: Any
) -> None:
    now = timezone.now()
    campaign = campaign_factory(
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=3),
        status=Campaign.Status.IN_PROGRESS,
    )
    Mission.objects.create(campaign=campaign, action=action, target_count=1)
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    institutional = Clan.objects.create(
        name="Institutional",
        type=Clan.ClanType.INSTITUTIONAL,
        created_by=admin_user,
    )
    log = ActionLog.objects.create(
        user=user,
        action=action,
        institutional_clan=institutional,
        idempotency_key="mission-notification",
        points_awarded=action.points,
        status=ActionLog.Status.APPROVED,
    )

    apply_action_log_to_missions(log)

    notification = Notification.objects.get(user=user)
    assert (
        notification.notification_type
        == Notification.NotificationType.MISSION_COMPLETED
    )
    assert action.name in notification.message


@pytest.mark.django_db
def test_global_campaign_invite_reaches_active_users_across_batches(
    user: User,
) -> None:
    audience = BULK_CREATE_BATCH_SIZE + 1
    User.objects.bulk_create(
        [User(email=f"active{index}@example.test") for index in range(audience)]
    )
    User.objects.bulk_create([User(email="inactive@example.test", is_active=False)])
    campaign = Campaign.objects.create(
        **campaign_data(creator=user, scope=Campaign.Scope.GLOBAL)
    )

    sent = notify_campaign_invite(campaign)

    assert sent == audience
    assert Notification.objects.count() == audience
    assert not Notification.objects.filter(user=user).exists()
    assert not Notification.objects.filter(user__email="inactive@example.test").exists()


@pytest.mark.django_db
def test_campaign_invite_without_recipients_sends_nothing(user: User) -> None:
    campaign = Campaign.objects.create(**campaign_data(creator=user))

    with patch("green_iteso.notifications.services.push_notifications") as push:
        sent = notify_campaign_invite(campaign)

    assert sent == 0
    assert not Notification.objects.exists()
    push.assert_not_called()
