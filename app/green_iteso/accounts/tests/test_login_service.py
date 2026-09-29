"""Coverage for services.login_with_microsoft: BR-01, upsert, and JWT issuance."""

from __future__ import annotations

import threading

import pytest
from django.db import connection
from django.test import TransactionTestCase

from green_iteso.accounts.exceptions import (
    AccountConflictError,
    AccountDisabledError,
    CannotDemoteLastAdminError,
    DomainNotAllowedError,
    RequestValidationError,
)
from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.models import User, UserProfile, UserRoleAudit
from green_iteso.accounts.services import (
    determine_initial_role,
    is_institutional_email,
    login_with_microsoft,
    update_user_role,
)


class _StubProvider:
    def __init__(self, identity: ExternalIdentity) -> None:
        self._identity = identity

    def authenticate(  # pylint: disable=unused-argument
        self, *, id_token: str, access_token: str
    ) -> ExternalIdentity:
        return self._identity


def _identity(**overrides: object) -> ExternalIdentity:
    defaults: dict[str, object] = {
        "oid": "33333333-3333-3333-3333-333333333333",
        "tenant_id": "tenant",
        "email": "ana@iteso.mx",
        "given_name": "Ana",
        "surname": "García",
        "job_title": "",
        "department": "Ingeniería de Software",
        "employee_id": "A01234567",
        "group_ids": ("g1",),
    }
    defaults.update(overrides)
    return ExternalIdentity(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("student@iteso.mx", True),
        ("STUDENT@ITESO.MX", True),
        ("student@gmail.com", False),
        ("student@evil-iteso.mx", False),
        ("student@iteso.mx.evil.com", False),
        ("@iteso.mx", False),
        ("student@", False),
    ],
)
def test_is_institutional_email(email: str, expected: bool) -> None:
    assert is_institutional_email(email) is expected


@pytest.mark.django_db
def test_first_login_creates_user_and_profile() -> None:
    result = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )

    assert result.created is True
    assert result.user.email == "ana@iteso.mx"
    assert result.user.role == User.Role.STUDENT
    assert result.access and result.refresh
    profile = UserProfile.objects.get(user=result.user)
    assert profile.department == "Ingeniería de Software"
    assert profile.microsoft_group_ids == ["g1"]


@pytest.mark.django_db
def test_second_login_reuses_the_user_and_refreshes_profile_fields() -> None:
    first = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )

    second = login_with_microsoft(
        id_token="t",
        access_token="a",
        provider=_StubProvider(_identity(department="Sustentabilidad")),
    )

    assert second.created is False
    assert second.user.pk == first.user.pk
    assert UserProfile.objects.get(user=first.user).department == "Sustentabilidad"


@pytest.mark.django_db
def test_login_links_an_existing_email_without_a_microsoft_oid() -> None:
    existing = User.objects.create_user(email="ana@iteso.mx", password="local-only")

    result = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )

    assert result.user.pk == existing.pk
    existing.refresh_from_db()
    assert str(existing.microsoft_oid) == "33333333-3333-3333-3333-333333333333"


@pytest.mark.django_db
def test_login_refuses_to_relink_an_email_already_bound_to_another_microsoft_account() -> (
    None
):
    other = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    other.microsoft_oid = "44444444-4444-4444-4444-444444444444"
    other.save()

    with pytest.raises(AccountConflictError):
        login_with_microsoft(
            id_token="t", access_token="a", provider=_StubProvider(_identity())
        )


@pytest.mark.django_db
def test_domain_not_allowed_rejects_non_institutional_email() -> None:
    with pytest.raises(DomainNotAllowedError):
        login_with_microsoft(
            id_token="t",
            access_token="a",
            provider=_StubProvider(_identity(email="ana@gmail.com")),
        )


@pytest.mark.django_db
def test_domain_not_allowed_rejects_lookalike_domain() -> None:
    with pytest.raises(DomainNotAllowedError):
        login_with_microsoft(
            id_token="t",
            access_token="a",
            provider=_StubProvider(_identity(email="ana@iteso.mx.evil.com")),
        )


@pytest.mark.django_db
def test_inactive_account_cannot_log_in() -> None:
    login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )
    User.objects.filter(email="ana@iteso.mx").update(is_active=False)

    with pytest.raises(AccountDisabledError):
        login_with_microsoft(
            id_token="t", access_token="a", provider=_StubProvider(_identity())
        )


class ConcurrentFirstLoginTests(TransactionTestCase):
    """Two simultaneous first logins for the same identity create one user."""

    def test_only_one_user_is_created(self) -> None:
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def attempt() -> None:
            try:
                barrier.wait(timeout=5)
                login_with_microsoft(
                    id_token="t", access_token="a", provider=_StubProvider(_identity())
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors, errors
        assert User.objects.filter(email="ana@iteso.mx").count() == 1


def test_determine_initial_role_defaults_to_student() -> None:
    assert (
        determine_initial_role(_identity(job_title=""), "student@iteso.mx")
        == User.Role.STUDENT
    )


@pytest.mark.django_db
def test_first_login_assigns_staff_when_job_title_present() -> None:
    result = login_with_microsoft(
        id_token="t",
        access_token="a",
        provider=_StubProvider(_identity(job_title="Profesor de Asignatura")),
    )

    assert result.created is True
    assert result.user.role == User.Role.STAFF


@pytest.mark.django_db
def test_first_login_assigns_staff_when_in_staff_emails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "green_iteso.accounts.services.settings.STAFF_EMAILS", ["staff_user@iteso.mx"]
    )
    result = login_with_microsoft(
        id_token="t",
        access_token="a",
        provider=_StubProvider(_identity(email="staff_user@iteso.mx", job_title="")),
    )

    assert result.created is True
    assert result.user.role == User.Role.STAFF


@pytest.mark.django_db
def test_subsequent_login_does_not_override_role_assigned_by_admin() -> None:
    first = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )
    assert first.user.role == User.Role.STUDENT

    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    update_user_role(admin_user=admin, user=first.user, new_role=User.Role.ADMIN)

    second = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )
    assert second.created is False
    assert second.user.role == User.Role.ADMIN


@pytest.mark.django_db
def test_update_user_role_creates_audit_record() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(
        email="student@iteso.mx", password="p", role=User.Role.STUDENT
    )

    updated = update_user_role(admin_user=admin, user=target, new_role=User.Role.STAFF)

    assert updated.role == User.Role.STAFF
    audit = UserRoleAudit.objects.get(user=target)
    assert audit.changed_by == admin
    assert audit.previous_role == User.Role.STUDENT
    assert audit.new_role == User.Role.STAFF
    assert audit.created_at is not None


@pytest.mark.django_db
def test_update_user_role_same_role_is_noop() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(
        email="student@iteso.mx", password="p", role=User.Role.STUDENT
    )

    updated = update_user_role(
        admin_user=admin, user=target, new_role=User.Role.STUDENT
    )

    assert updated.role == User.Role.STUDENT
    assert UserRoleAudit.objects.filter(user=target).count() == 0


@pytest.mark.django_db
def test_update_user_role_invalid_role_raises_validation_error() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(
        email="student@iteso.mx", password="p", role=User.Role.STUDENT
    )

    with pytest.raises(RequestValidationError):
        update_user_role(admin_user=admin, user=target, new_role="INVALID")


@pytest.mark.django_db
def test_update_user_role_prevent_demoting_only_active_admin() -> None:
    admin = User.objects.create_user(
        email="sole_admin@iteso.mx", password="p", role=User.Role.ADMIN
    )

    with pytest.raises(CannotDemoteLastAdminError):
        update_user_role(admin_user=admin, user=admin, new_role=User.Role.STUDENT)
