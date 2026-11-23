"""Login institucional: flujo completo de POST /auth/login/.

Referencia: ID01 ("Identidad 1" en la división de trabajo del equipo) = login
institucional con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o
actualización del usuario y rol según el puesto.

Caja negra: se llama al endpoint como lo haría el front y solo se revisa la
respuesta y lo que quedó en la base. Caja blanca: se llama al servicio
``login_with_microsoft`` con un proveedor falso para forzar casos internos
(Graph caído, correo cambiado, cuenta en conflicto).

Usa el proveedor mock (``mock:<correo>``) para no llamar a Microsoft.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.exceptions import (
    AccountConflictError,
    AccountDisabledError,
    DomainNotAllowedError,
    IdentityProviderUnavailableError,
)
from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.models import User, UserProfile
from green_iteso.accounts.services import login_with_microsoft

LOGIN_URL = "/api/v1/auth/login/"


pytestmark = pytest.mark.usefixtures("modo_mock")


def _login(token: str, client: APIClient | None = None) -> object:
    return (client or APIClient()).post(
        LOGIN_URL, {"id_token": token, "access_token": ""}, format="json"
    )


def _mock_json(**claims: object) -> str:
    return "mock:" + json.dumps(claims)


class _FixedProvider:
    """Proveedor de prueba que devuelve siempre la identidad que se le da."""

    def __init__(self, identity: ExternalIdentity) -> None:
        self.identity = identity

    def authenticate(self, *, id_token: str, access_token: str) -> ExternalIdentity:
        del id_token, access_token
        return self.identity


def _identity(email: str = "ana@iteso.mx", **extra: object) -> ExternalIdentity:
    return ExternalIdentity(oid=str(uuid.uuid4()), tenant_id="t", email=email, **extra)


# ---------------------------------------------------------------------------
# Alta y actualización del usuario
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_first_login_creates_user_and_profile() -> None:
    response = _login("mock:ana@iteso.mx")
    assert response.status_code == 200
    assert response.json()["created"] is True
    user = User.objects.get(email="ana@iteso.mx")
    assert UserProfile.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_second_login_reuses_the_same_user() -> None:
    _login("mock:ana@iteso.mx")
    response = _login("mock:ana@iteso.mx")
    assert response.json()["created"] is False
    assert User.objects.filter(email="ana@iteso.mx").count() == 1


@pytest.mark.django_db
def test_login_stores_email_in_lowercase() -> None:
    _login("mock:ANA@ITESO.MX")
    assert User.objects.filter(email="ana@iteso.mx").exists()


@pytest.mark.django_db
def test_login_returns_access_and_refresh_tokens() -> None:
    body = _login("mock:ana@iteso.mx").json()
    assert body["access"] and body["refresh"]
    assert body["access"] != body["refresh"]


@pytest.mark.django_db
def test_login_response_never_includes_password_or_oid() -> None:
    user = _login("mock:ana@iteso.mx").json()["user"]
    assert "password" not in user
    assert "microsoft_oid" not in user


@pytest.mark.django_db
def test_login_new_user_has_unusable_password() -> None:
    _login("mock:ana@iteso.mx")
    assert User.objects.get(email="ana@iteso.mx").has_usable_password() is False


@pytest.mark.django_db
def test_login_sets_last_login() -> None:
    _login("mock:ana@iteso.mx")
    assert User.objects.get(email="ana@iteso.mx").last_login is not None


@pytest.mark.django_db
def test_login_rejects_other_domain_with_403_envelope() -> None:
    response = _login("mock:ana@gmail.com")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DOMAIN_NOT_ALLOWED"
    assert not User.objects.filter(email="ana@gmail.com").exists()


@pytest.mark.django_db
def test_login_rejects_subdomain() -> None:
    assert _login("mock:ana@alumnos.iteso.mx").status_code == 403


@pytest.mark.django_db
def test_login_without_id_token_is_400_envelope() -> None:
    response = APIClient().post(LOGIN_URL, {}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.django_db
def test_login_with_blank_id_token_is_400() -> None:
    response = APIClient().post(LOGIN_URL, {"id_token": "   "}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_with_huge_token_is_400() -> None:
    response = APIClient().post(LOGIN_URL, {"id_token": "x" * 9000}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_invalid_mock_token_is_401_envelope() -> None:
    response = _login("no-mock")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.django_db
def test_login_get_method_not_allowed() -> None:
    assert APIClient().get(LOGIN_URL).status_code == 405


@pytest.mark.django_db
def test_login_copies_names_from_directory() -> None:
    _login(_mock_json(email="ana@iteso.mx", given_name="Ana", surname="García"))
    user = User.objects.get(email="ana@iteso.mx")
    assert (user.first_name, user.last_name) == ("Ana", "García")


@pytest.mark.django_db
def test_login_clips_very_long_names() -> None:
    _login(_mock_json(email="ana@iteso.mx", given_name="A" * 400))
    assert len(User.objects.get(email="ana@iteso.mx").first_name) == 150


@pytest.mark.django_db
def test_login_syncs_directory_fields_to_profile() -> None:
    _login(
        _mock_json(
            email="ana@iteso.mx", department="DESI", employee_id="99", group_ids=["g1"]
        )
    )
    profile = UserProfile.objects.get(user__email="ana@iteso.mx")
    assert profile.department == "DESI"
    assert profile.employee_id == "99"
    assert profile.microsoft_group_ids == ["g1"]


@pytest.mark.django_db
def test_login_without_directory_fields_keeps_stored_values() -> None:
    _login(_mock_json(email="ana@iteso.mx", department="DESI"))
    _login("mock:ana@iteso.mx")
    assert UserProfile.objects.get(user__email="ana@iteso.mx").department == "DESI"


@pytest.mark.django_db
def test_login_links_oid_to_existing_user_without_oid() -> None:
    # Un usuario creado antes (seed/admin) se vincula en su primer login.
    User.objects.create_user(email="ana@iteso.mx")
    response = _login("mock:ana@iteso.mx")
    assert response.json()["created"] is False
    assert User.objects.get(email="ana@iteso.mx").microsoft_oid is not None


@pytest.mark.django_db
def test_login_email_already_linked_to_other_oid_is_conflict() -> None:
    User.objects.create_user(email="ana@iteso.mx", microsoft_oid=uuid.uuid4())
    response = _login("mock:ana@iteso.mx")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_login_admin_disabled_account_is_403() -> None:
    _login("mock:ana@iteso.mx")
    User.objects.filter(email="ana@iteso.mx").update(is_active=False)
    response = _login("mock:ana@iteso.mx")
    assert response.status_code == 403


@pytest.mark.django_db
def test_login_reactivates_self_deactivated_account() -> None:
    _login("mock:ana@iteso.mx")
    User.objects.filter(email="ana@iteso.mx").update(
        is_active=False, deactivated_at=timezone.now()
    )
    body = _login("mock:ana@iteso.mx").json()
    assert body["reactivated"] is True
    user = User.objects.get(email="ana@iteso.mx")
    assert user.is_active is True
    assert user.deactivated_at is None


@pytest.mark.django_db
def test_login_normal_account_is_not_marked_reactivated() -> None:
    _login("mock:ana@iteso.mx")
    assert _login("mock:ana@iteso.mx").json()["reactivated"] is False


# ---------------------------------------------------------------------------
# Rol según el puesto
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_assigns_staff_from_job_title() -> None:
    body = _login(_mock_json(email="prof@iteso.mx", job_title="Profesor")).json()
    assert body["user"]["role"] == "STAFF"


@pytest.mark.django_db
def test_login_without_job_title_is_student() -> None:
    assert _login("mock:ana@iteso.mx").json()["user"]["role"] == "STUDENT"


@pytest.mark.django_db
def test_login_promotes_student_when_title_changes_to_staff() -> None:
    _login(_mock_json(email="ana@iteso.mx", job_title="Alumno"))
    _login(_mock_json(email="ana@iteso.mx", job_title="Docente"))
    assert User.objects.get(email="ana@iteso.mx").role == User.Role.STAFF


@pytest.mark.django_db
def test_login_never_demotes_admin() -> None:
    _login("mock:ana@iteso.mx")
    User.objects.filter(email="ana@iteso.mx").update(role=User.Role.ADMIN)
    _login(_mock_json(email="ana@iteso.mx", job_title="Alumno"))
    assert User.objects.get(email="ana@iteso.mx").role == User.Role.ADMIN


@pytest.mark.django_db
def test_login_unknown_title_does_not_demote_staff() -> None:
    # Si el directorio no dijo el puesto (None) no se toca el rol.
    _login(_mock_json(email="prof@iteso.mx", job_title="Profesor"))
    _login("mock:prof@iteso.mx")
    assert User.objects.get(email="prof@iteso.mx").role == User.Role.STAFF


@pytest.mark.django_db
def test_service_new_user_without_directory_is_503() -> None:
    identity = _identity(directory_profile_available=False)
    with pytest.raises(IdentityProviderUnavailableError):
        login_with_microsoft(
            id_token="x", access_token="", provider=_FixedProvider(identity)
        )


@pytest.mark.django_db
def test_service_existing_user_logs_in_when_graph_is_down() -> None:
    identity = _identity()
    login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(identity)
    )
    down = ExternalIdentity(
        oid=identity.oid,
        tenant_id="t",
        email=identity.email,
        directory_profile_available=False,
    )
    result = login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(down)
    )
    assert result.created is False


@pytest.mark.django_db
def test_service_rejects_other_domain() -> None:
    with pytest.raises(DomainNotAllowedError):
        login_with_microsoft(
            id_token="x",
            access_token="",
            provider=_FixedProvider(_identity("a@hotmail.com")),
        )


@pytest.mark.django_db
def test_service_conflict_error_type() -> None:
    User.objects.create_user(email="ana@iteso.mx", microsoft_oid=uuid.uuid4())
    with pytest.raises(AccountConflictError):
        login_with_microsoft(
            id_token="x", access_token="", provider=_FixedProvider(_identity())
        )


@pytest.mark.django_db
def test_service_disabled_error_type() -> None:
    identity = _identity()
    login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(identity)
    )
    User.objects.filter(email=identity.email).update(is_active=False)
    with pytest.raises(AccountDisabledError):
        login_with_microsoft(
            id_token="x", access_token="", provider=_FixedProvider(identity)
        )


@pytest.mark.django_db
def test_service_email_change_in_directory_updates_user() -> None:
    identity = _identity("viejo@iteso.mx")
    login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(identity)
    )
    renamed = ExternalIdentity(oid=identity.oid, tenant_id="t", email="nuevo@iteso.mx")
    login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(renamed)
    )
    assert User.objects.filter(email="nuevo@iteso.mx").exists()


@pytest.mark.django_db
def test_service_email_change_to_taken_email_keeps_old_email() -> None:
    User.objects.create_user(email="ocupado@iteso.mx")
    identity = _identity("viejo@iteso.mx")
    login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(identity)
    )
    renamed = ExternalIdentity(
        oid=identity.oid, tenant_id="t", email="ocupado@iteso.mx"
    )
    result = login_with_microsoft(
        id_token="x", access_token="", provider=_FixedProvider(renamed)
    )
    assert result.user.email == "viejo@iteso.mx"
