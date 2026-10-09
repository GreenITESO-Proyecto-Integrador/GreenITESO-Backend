"""Coverage for accounts admin form validation and queryset overrides."""

from __future__ import annotations

import threading

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from green_iteso.accounts.admin import (
    ClanAdmin,
    ClanMembershipAdmin,
    ClanMembershipAdminForm,
)
from green_iteso.accounts.models import Clan, ClanMembership, User


def _membership_admin() -> ClanMembershipAdmin:
    return ClanMembershipAdmin(ClanMembership, AdminSite())


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


@pytest.mark.django_db
def test_clan_membership_admin_save_model_allows_first_leader() -> None:
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    obj = ClanMembership(
        user=owner, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    request = RequestFactory().post("/admin/accounts/clanmembership/add/")

    _membership_admin().save_model(
        request, obj, ClanMembershipAdminForm(), change=False
    )

    assert ClanMembership.objects.get(pk=obj.pk).role == (
        ClanMembership.MembershipRole.LEADER
    )


@pytest.mark.django_db
def test_clan_membership_admin_save_model_rejects_second_leader_for_same_clan() -> None:
    """The atomic recheck in save_model enforces the rule on its own.

    Called directly, bypassing ClanMembershipAdminForm.clean() entirely, so
    this proves save_model is itself a real enforcement point and not just a
    backstop that happens to agree with the form.
    """
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    candidate = User.objects.create_user(
        email="candidate@iteso.mx", password="local-only"
    )
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=owner, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    obj = ClanMembership(
        user=candidate, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    request = RequestFactory().post("/admin/accounts/clanmembership/add/")

    with pytest.raises(ValidationError):
        _membership_admin().save_model(
            request, obj, ClanMembershipAdminForm(), change=False
        )

    assert not ClanMembership.objects.filter(pk=obj.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_clan_membership_admin_save_model_blocks_concurrent_duplicate_leaders() -> None:
    """Regression test for Copilot's finding: the admin path had no lock.

    ``ClanMembershipAdminForm.clean()`` runs unlocked, so two concurrent
    admin submissions could both pass it and both save a LEADER row. This
    reproduces that race directly against ``save_model`` (skipping the form
    layer, which is not what serializes the two calls) and asserts only one
    of the two succeeds.
    """
    owner = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    challenger = User.objects.create_user(
        email="challenger@iteso.mx", password="local-only"
    )
    clan = Clan.objects.create(name="Green Team", type=Clan.ClanType.PRIVATE)
    owner_obj = ClanMembership(
        user=owner, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    challenger_obj = ClanMembership(
        user=challenger, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    request = RequestFactory().post("/admin/accounts/clanmembership/add/")
    barrier = threading.Barrier(2)
    errors: list[Exception | None] = [None, None]

    def submit(index: int, obj: ClanMembership) -> None:
        try:
            barrier.wait(timeout=5)
            _membership_admin().save_model(
                request, obj, ClanMembershipAdminForm(), change=False
            )
        except ValidationError as exc:
            errors[index] = exc
        finally:
            connection.close()

    threads = [
        threading.Thread(target=submit, args=(0, owner_obj)),
        threading.Thread(target=submit, args=(1, challenger_obj)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    leader_count = ClanMembership.objects.filter(
        clan=clan, role=ClanMembership.MembershipRole.LEADER
    ).count()
    assert leader_count == 1
    assert sum(1 for error in errors if error is not None) == 1
