"""Coverage for accounts management commands."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from green_iteso.accounts.models import User, UserRoleAudit


@pytest.mark.django_db
def test_assign_user_role_updates_role_and_creates_audit() -> None:
    user = User.objects.create_user(
        email="target@iteso.mx", password="p", role=User.Role.STUDENT
    )
    out = io.StringIO()

    call_command("assign_user_role", "target@iteso.mx", "ADMIN", stdout=out)

    user.refresh_from_db()
    assert user.role == User.Role.ADMIN
    assert "Successfully updated role" in out.getvalue()

    audit = UserRoleAudit.objects.get(user=user)
    assert audit.changed_by is None
    assert audit.previous_role == "STUDENT"
    assert audit.new_role == "ADMIN"


@pytest.mark.django_db
def test_assign_user_role_warns_when_role_already_assigned() -> None:
    User.objects.create_user(email="admin@iteso.mx", password="p", role=User.Role.ADMIN)
    out = io.StringIO()

    call_command("assign_user_role", "admin@iteso.mx", "ADMIN", stdout=out)

    assert "already has role ADMIN" in out.getvalue()
    assert UserRoleAudit.objects.count() == 0


@pytest.mark.django_db
def test_assign_user_role_raises_error_for_nonexistent_user() -> None:
    with pytest.raises(CommandError, match="does not exist"):
        call_command("assign_user_role", "nobody@iteso.mx", "ADMIN")


@pytest.mark.django_db
def test_assign_user_role_cannot_demote_only_active_admin() -> None:
    User.objects.create_user(
        email="admin@iteso.mx", password=None, role=User.Role.ADMIN
    )

    with pytest.raises(CommandError, match="only active administrator"):
        call_command("assign_user_role", "admin@iteso.mx", "STUDENT")
