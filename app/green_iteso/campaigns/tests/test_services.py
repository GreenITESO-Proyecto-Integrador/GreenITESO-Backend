"""Tests for the campaign authorization services."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.campaigns.models import Campaign
from green_iteso.campaigns.services import can_manage_campaign


@pytest.mark.django_db
class TestCanManageCampaign:
    def test_admin_manages_global_campaign(
        self, admin_user: User, campaign: Campaign
    ) -> None:
        assert can_manage_campaign(admin_user, campaign) is True

    def test_admin_cannot_manage_private_campaign(
        self, admin_user: User, private_campaign: Campaign
    ) -> None:
        assert can_manage_campaign(admin_user, private_campaign) is False

    def test_leader_manages_own_private_campaign(
        self, user: User, clan: Clan, private_campaign: Campaign
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )

        assert can_manage_campaign(user, private_campaign) is True

    def test_member_cannot_manage_private_campaign(
        self, user: User, clan: Clan, private_campaign: Campaign
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
        )

        assert can_manage_campaign(user, private_campaign) is False

    def test_leader_cannot_manage_global_campaign(
        self, user: User, clan: Clan, campaign: Campaign
    ) -> None:
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.LEADER
        )

        assert can_manage_campaign(user, campaign) is False
