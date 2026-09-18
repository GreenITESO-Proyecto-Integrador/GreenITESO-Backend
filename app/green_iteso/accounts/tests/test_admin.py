"""Coverage for accounts admin form validation."""

from __future__ import annotations

import pytest

from green_iteso.accounts.admin import ClanMembershipAdminForm
from green_iteso.accounts.models import Clan, ClanMembership, User


@pytest.mark.django_db
def test_clan_membership_admin_form_rejects_second_leader_for_same_clan() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    candidate = User.objects.create_user(
        email="candidate@iteso.mx", password="local-only"
    )
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=owner, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    form = ClanMembershipAdminForm(
        data={
            "user": candidate.pk,
            "clan": clan.pk,
            "role": ClanMembership.MembershipRole.LEADER,
            "is_active_private": False,
        }
    )

    assert not form.is_valid()
    assert "already has a LEADER membership" in str(form.errors)


@pytest.mark.django_db
def test_clan_membership_admin_form_allows_first_leader() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)

    form = ClanMembershipAdminForm(
        data={
            "user": owner.pk,
            "clan": clan.pk,
            "role": ClanMembership.MembershipRole.LEADER,
            "is_active_private": False,
        }
    )

    assert form.is_valid(), form.errors
