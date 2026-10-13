"""Tests for campaign invitation recipients."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.notifications import notify_campaign_invite
from green_iteso.campaigns.tests.helpers import campaign_data
from green_iteso.notifications.models import Notification


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
