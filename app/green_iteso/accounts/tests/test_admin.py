"""Coverage for accounts admin form validation and queryset overrides."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from green_iteso.accounts.admin import ClanAdmin, ClanMembershipAdminForm
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


@pytest.mark.django_db
def test_clan_admin_get_queryset_includes_soft_deleted_clans() -> None:
    alive = Clan.objects.create(name="Alive", type=Clan.ClanType.PRIVATE)
    deleted = Clan.objects.create(
        name="Deleted", type=Clan.ClanType.PRIVATE, deleted_at=timezone.now()
    )
    admin_instance = ClanAdmin(Clan, AdminSite())
    request = RequestFactory().get("/admin/accounts/clan/")

    queryset = admin_instance.get_queryset(request)

    assert set(queryset) == {alive, deleted}


@pytest.mark.django_db
def test_clan_admin_get_queryset_still_applies_ordering() -> None:
    """Regression test: the previous override skipped ModelAdmin's ordering step."""
    Clan.objects.create(name="Zebra clan", type=Clan.ClanType.PRIVATE)
    Clan.objects.create(name="Alpha clan", type=Clan.ClanType.PRIVATE)
    admin_instance = ClanAdmin(Clan, AdminSite())
    admin_instance.ordering = ("name",)
    request = RequestFactory().get("/admin/accounts/clan/")

    names = list(admin_instance.get_queryset(request).values_list("name", flat=True))

    assert names == ["Alpha clan", "Zebra clan"]
