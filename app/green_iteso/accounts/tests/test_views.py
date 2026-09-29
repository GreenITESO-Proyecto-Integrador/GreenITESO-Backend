"""Coverage for the /api/v1/users/me/ router."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User


@pytest.mark.django_db
def test_me_requires_authentication() -> None:
    response = APIClient().get("/api/v1/users/me/")

    # JWTAuthentication is active (T2-10), so a missing token is 401, not 403.
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_returns_the_authenticated_caller_and_not_another_account() -> None:
    caller = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    User.objects.create_user(email="zoe@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(caller)

    response = client.get("/api/v1/users/me/")

    assert response.status_code == 200
    assert response.json()["email"] == "ana@iteso.mx"


@pytest.mark.django_db
def test_list_users_requires_admin_role() -> None:
    student = User.objects.create_user(email="student@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(student)

    response = client.get("/api/v1/users/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_list_users_returns_all_accounts_for_admin() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    User.objects.create_user(email="ana@iteso.mx", password="local-only")
    client = APIClient()
    client.force_authenticate(admin)

    response = client.get("/api/v1/users/")

    assert response.status_code == 200
    emails = {row["email"] for row in response.json()["results"]}
    assert emails == {"admin@iteso.mx", "ana@iteso.mx"}


@pytest.mark.django_db
def test_change_role_requires_authentication() -> None:
    target = User.objects.create_user(email="target@iteso.mx", password="p")
    response = APIClient().patch(f"/api/v1/users/{target.pk}/role/", {"role": "STAFF"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_change_role_forbidden_for_student() -> None:
    student = User.objects.create_user(
        email="student@iteso.mx", password="p", role=User.Role.STUDENT
    )
    target = User.objects.create_user(email="target@iteso.mx", password="p")
    client = APIClient()
    client.force_authenticate(student)

    response = client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "STAFF"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_change_role_forbidden_for_staff() -> None:
    staff = User.objects.create_user(
        email="staff@iteso.mx", password="p", role=User.Role.STAFF
    )
    target = User.objects.create_user(email="target@iteso.mx", password="p")
    client = APIClient()
    client.force_authenticate(staff)

    response = client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "ADMIN"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_change_role_success_by_admin() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(
        email="target@iteso.mx", password="p", role=User.Role.STUDENT
    )
    client = APIClient()
    client.force_authenticate(admin)

    response = client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "STAFF"})
    assert response.status_code == 200
    assert response.json()["role"] == "STAFF"

    target.refresh_from_db()
    assert target.role == User.Role.STAFF

    # Verify audit record was created
    audit = target.role_audit_logs.first()
    assert audit is not None
    assert audit.changed_by == admin
    assert audit.previous_role == "STUDENT"
    assert audit.new_role == "STAFF"


@pytest.mark.django_db
def test_change_role_invalid_role_returns_400() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(email="target@iteso.mx", password="p")
    client = APIClient()
    client.force_authenticate(admin)

    response = client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "SUPERUSER"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_change_role_nonexistent_user_returns_404() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    client = APIClient()
    client.force_authenticate(admin)

    nonexistent_id = uuid.uuid4()
    response = client.patch(f"/api/v1/users/{nonexistent_id}/role/", {"role": "STAFF"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_change_role_prevent_demoting_only_admin() -> None:
    admin = User.objects.create_user(
        email="sole_admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    client = APIClient()
    client.force_authenticate(admin)

    response = client.patch(f"/api/v1/users/{admin.pk}/role/", {"role": "STUDENT"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_role_history_requires_admin() -> None:
    student = User.objects.create_user(
        email="student@iteso.mx", password="p", role=User.Role.STUDENT
    )
    target = User.objects.create_user(email="target@iteso.mx", password="p")
    client = APIClient()
    client.force_authenticate(student)

    response = client.get(f"/api/v1/users/{target.pk}/role-history/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_role_history_returns_audits_for_admin() -> None:
    admin = User.objects.create_user(
        email="admin@iteso.mx", password="p", role=User.Role.ADMIN
    )
    target = User.objects.create_user(
        email="target@iteso.mx", password="p", role=User.Role.STUDENT
    )
    client = APIClient()
    client.force_authenticate(admin)

    client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "STAFF"})
    client.patch(f"/api/v1/users/{target.pk}/role/", {"role": "ADMIN"})

    response = client.get(f"/api/v1/users/{target.pk}/role-history/")
    assert response.status_code == 200
    audits = response.json()
    assert len(audits) == 2
    assert audits[0]["previous_role"] == "STAFF"
    assert audits[0]["new_role"] == "ADMIN"
    assert audits[1]["previous_role"] == "STUDENT"
    assert audits[1]["new_role"] == "STAFF"
