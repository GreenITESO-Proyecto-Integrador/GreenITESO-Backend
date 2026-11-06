"""Deactivating an account coexists with a concurrent action approval (#176)."""

from __future__ import annotations

import pytest

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.accounts.services import deactivate_account, ensure_profile
from green_iteso.accounts.tests.lock_helpers import ApprovalRace
from green_iteso.actions.models import ActionLog
from green_iteso.actions.tests.helpers import create_pending_photo_audit_log


@pytest.mark.django_db(transaction=True)
def test_deactivation_does_not_deadlock_with_action_approval() -> None:
    """Approval's notification FK check must coexist with the user lock."""
    user = User.objects.create_user(
        email="deactivation-audit-concurrency@iteso.mx", nickname="Existing"
    )
    admin = User.objects.create_user(
        email="deactivation-audit-admin@iteso.mx", role=User.Role.ADMIN
    )
    clan = Clan.objects.create(name="Audit clan", type=Clan.ClanType.INSTITUTIONAL)
    UserProfile.objects.create(user=user, institutional_clan=clan)
    log = create_pending_photo_audit_log(user, clan)

    ApprovalRace(admin=admin, log=log).run(
        lambda: deactivate_account(user=User.objects.get(pk=user.pk))
    )

    log.refresh_from_db()
    user.refresh_from_db()
    profile = ensure_profile(user)
    assert log.status == ActionLog.Status.APPROVED
    assert user.is_active is False
    assert user.deactivated_at is not None
    assert profile.available_points == 0
    assert profile.total_points == 0
