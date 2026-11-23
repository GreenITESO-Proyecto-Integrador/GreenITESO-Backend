"""Login institucional: proveedores de identidad (mock y Entra), dominio y rol por puesto.

Referencia: ID01 ("Identidad 1" en la división de trabajo del equipo) = login
institucional con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o
actualización del usuario y rol según el puesto.

Caja blanca: se prueban las piezas internas del login una por una (verificación
del ID token, lectura de Graph, filtro @iteso.mx, regla de rol), sin base de datos.

Cubre lo que no depende de la base de datos: el proveedor mock, la
verificación del ID token de Entra, la lectura de Graph, el filtro del
dominio @iteso.mx y la regla que decide el rol según el puesto.
"""

# pylint: disable=redefined-outer-name,protected-access

from __future__ import annotations

import json
import uuid

import httpx
import jwt
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from green_iteso.accounts.exceptions import (
    IdentityProviderUnavailableError,
    InvalidIdentityTokenError,
)
from green_iteso.accounts.identity import get_identity_provider
from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.identity.entra import GRAPH_BASE, EntraProvider, _first_text
from green_iteso.accounts.identity.mock import MockProvider
from green_iteso.accounts.models import User
from green_iteso.accounts.services import (
    _clip,
    _normalize_job_title,
    determine_initial_role,
    is_institutional_email,
)
from green_iteso.accounts.tests.identity_helpers import (
    CLIENT_ID,
    TENANT_ID,
    RecordingTransport,
    StubKeyResolver,
    make_id_token,
    make_keypair,
)

# ---------------------------------------------------------------------------
# Proveedor mock
# ---------------------------------------------------------------------------


def _mock(token: str) -> ExternalIdentity:
    return MockProvider().authenticate(id_token=token, access_token="")


def test_mock_accepts_plain_email() -> None:
    # El formato más simple: mock:<correo>.
    identity = _mock("mock:ana@iteso.mx")
    assert identity.email == "ana@iteso.mx"


def test_mock_trims_spaces_around_email() -> None:
    identity = _mock("mock:   ana@iteso.mx   ")
    assert identity.email == "ana@iteso.mx"


def test_mock_rejects_token_without_prefix() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        _mock("ana@iteso.mx")


def test_mock_rejects_empty_email() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        _mock("mock:")


def test_mock_rejects_invalid_json() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        _mock("mock:{not json")


def test_mock_rejects_json_without_email() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        _mock('mock:{"department": "DESI"}')


def test_mock_reads_directory_fields_from_json() -> None:
    payload = {
        "email": "prof@iteso.mx",
        "given_name": "Luis",
        "surname": "Pérez",
        "job_title": "Profesor",
        "department": "DESI",
        "employee_id": "123",
    }
    identity = _mock("mock:" + json.dumps(payload))
    assert identity.given_name == "Luis"
    assert identity.surname == "Pérez"
    assert identity.job_title == "Profesor"
    assert identity.department == "DESI"
    assert identity.employee_id == "123"


def test_mock_missing_fields_are_none_so_stored_values_are_kept() -> None:
    # None significa "no se leyó", no "vacío": el login no debe borrar datos.
    identity = _mock("mock:ana@iteso.mx")
    assert identity.job_title is None
    assert identity.department is None
    assert identity.group_ids is None


def test_mock_present_empty_field_is_empty_string() -> None:
    identity = _mock('mock:{"email": "a@iteso.mx", "job_title": ""}')
    assert identity.job_title == ""


def test_mock_oid_is_stable_for_same_email() -> None:
    # Dos logins del mismo correo deben caer en el mismo usuario.
    assert _mock("mock:ana@iteso.mx").oid == _mock("mock:ana@iteso.mx").oid


def test_mock_oid_ignores_email_case() -> None:
    assert _mock("mock:ANA@iteso.mx").oid == _mock("mock:ana@iteso.mx").oid


def test_mock_oid_differs_between_users() -> None:
    assert _mock("mock:ana@iteso.mx").oid != _mock("mock:beto@iteso.mx").oid


def test_mock_oid_is_a_valid_uuid() -> None:
    uuid.UUID(_mock("mock:ana@iteso.mx").oid)


def test_mock_group_ids_list_becomes_tuple_of_strings() -> None:
    identity = _mock('mock:{"email": "a@iteso.mx", "group_ids": [1, "g2"]}')
    assert identity.group_ids == ("1", "g2")


def test_mock_group_ids_not_a_list_becomes_empty_tuple() -> None:
    identity = _mock('mock:{"email": "a@iteso.mx", "group_ids": "g1"}')
    assert identity.group_ids == ()


def test_mock_json_array_is_rejected() -> None:
    # "[...]" no empieza con "{", así que se toma como correo literal.
    identity = _mock('mock:["a@iteso.mx"]')
    assert identity.email == '["a@iteso.mx"]'


# ---------------------------------------------------------------------------
# Selección del proveedor según settings
# ---------------------------------------------------------------------------


@override_settings(MICROSOFT_AUTH_MODE="mock")
def test_get_provider_returns_mock_in_mock_mode() -> None:
    assert isinstance(get_identity_provider(), MockProvider)


@override_settings(
    MICROSOFT_AUTH_MODE="entra", MICROSOFT_TENANT_ID="", MICROSOFT_CLIENT_ID="x"
)
def test_get_provider_requires_tenant_id() -> None:
    with pytest.raises(ImproperlyConfigured):
        get_identity_provider()


@override_settings(
    MICROSOFT_AUTH_MODE="entra", MICROSOFT_TENANT_ID="x", MICROSOFT_CLIENT_ID=""
)
def test_get_provider_requires_client_id() -> None:
    with pytest.raises(ImproperlyConfigured):
        get_identity_provider()


# ---------------------------------------------------------------------------
# Proveedor Entra (ID token + Graph)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair() -> tuple[object, object]:
    return make_keypair()


def _graph(
    responses: dict[str, httpx.Response],
) -> tuple[httpx.Client, RecordingTransport]:
    transport = RecordingTransport(responses)
    return httpx.Client(transport=transport), transport


def _entra(
    public_key: object,
    responses: dict[str, httpx.Response] | None = None,
    *,
    fail_keys: bool = False,
    tenant_id: str = TENANT_ID,
) -> tuple[EntraProvider, RecordingTransport]:
    client, transport = _graph(responses or {})
    provider = EntraProvider(
        tenant_id=tenant_id,
        client_id=CLIENT_ID,
        key_resolver=StubKeyResolver(public_key, fail=fail_keys),
        http_client=client,
    )
    return provider, transport


def _me(oid: str, **extra: object) -> httpx.Response:
    body = {"id": oid, "mail": "student@iteso.mx", "givenName": "Ana", "surname": "G"}
    body.update(extra)
    return httpx.Response(200, json=body)


def _groups(*ids: str, next_link: str | None = None) -> httpx.Response:
    body: dict[str, object] = {"value": [{"id": g} for g in ids]}
    if next_link:
        body["@odata.nextLink"] = next_link
    return httpx.Response(200, json=body)


ME_PATH = "/v1.0/me"
GROUPS_PATH = "/v1.0/me/transitiveMemberOf/microsoft.graph.group"


def test_entra_valid_token_with_graph_profile(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, _ = _entra(
        public, {ME_PATH: _me(oid, jobTitle="Alumno"), GROUPS_PATH: _groups("g1")}
    )
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="at"
    )
    assert identity.oid == oid
    assert identity.job_title == "Alumno"
    assert identity.group_ids == ("g1",)
    assert identity.directory_profile_available is True


def test_entra_expired_token_is_rejected(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public)
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(
            id_token=make_id_token(private, expired=True), access_token=""
        )


def test_entra_wrong_audience_is_rejected(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public)
    token = make_id_token(private, audience="otra-app")
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="")


def test_entra_wrong_issuer_is_rejected(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public)
    token = make_id_token(private, issuer="https://evil.example/v2.0")
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="")


def test_entra_other_tenant_is_rejected(keypair: tuple) -> None:
    # Un token firmado bien pero de otro tenant (otra universidad) no entra.
    private, public = keypair
    provider, _ = _entra(public)
    other = "33333333-3333-3333-3333-333333333333"
    token = make_id_token(
        private,
        tenant_id=other,
        issuer=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    )
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="")


def test_entra_token_signed_with_other_key_is_rejected(keypair: tuple) -> None:
    _, public = keypair
    other_private, _ = make_keypair()
    provider, _ = _entra(public)
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=make_id_token(other_private), access_token="")


def test_entra_hs256_token_is_rejected(keypair: tuple) -> None:
    # Solo RS256: un token HMAC no debe pasar aunque tenga los claims correctos.
    _, public = keypair
    provider, _ = _entra(public)
    token = jwt.encode(
        {"oid": str(uuid.uuid4())},
        "llave-hmac-de-pruebas-con-32-bytes!!",
        algorithm="HS256",
    )
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=token, access_token="")


def test_entra_garbage_token_is_rejected(keypair: tuple) -> None:
    _, public = keypair
    provider, _ = _entra(public)
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token="no-es-un-jwt", access_token="")


def test_entra_non_uuid_oid_is_rejected(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public)
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(
            id_token=make_id_token(private, oid="no-uuid"), access_token=""
        )


def test_entra_jwks_unreachable_is_503(keypair: tuple) -> None:
    # Si Microsoft no responde, es un 503 y no un 401 (no es culpa del usuario).
    private, public = keypair
    provider, _ = _entra(public, fail_keys=True)
    with pytest.raises(IdentityProviderUnavailableError):
        provider.authenticate(id_token=make_id_token(private), access_token="")


def test_entra_mixed_case_tenant_setting_still_works(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, _ = _entra(
        public, {ME_PATH: _me(oid), GROUPS_PATH: _groups()}, tenant_id=TENANT_ID.upper()
    )
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="at"
    )
    assert identity.tenant_id == TENANT_ID


def test_entra_graph_profile_of_other_user_is_rejected(keypair: tuple) -> None:
    # El access token de otra persona no puede "prestar" su perfil.
    private, public = keypair
    provider, _ = _entra(public, {ME_PATH: _me(str(uuid.uuid4()))})
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(id_token=make_id_token(private), access_token="at")


def test_entra_graph_down_falls_back_to_token_claims(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public, {ME_PATH: httpx.Response(500)})
    identity = provider.authenticate(
        id_token=make_id_token(private, email="ana@iteso.mx"), access_token="at"
    )
    assert identity.email == "ana@iteso.mx"
    assert identity.directory_profile_available is False
    assert identity.job_title is None
    assert identity.group_ids is None


def test_entra_without_any_email_is_rejected(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public, {ME_PATH: httpx.Response(503)})
    with pytest.raises(InvalidIdentityTokenError):
        provider.authenticate(
            id_token=make_id_token(private, email=None), access_token=""
        )


def test_entra_prefers_graph_mail_over_token_email(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, _ = _entra(
        public, {ME_PATH: _me(oid, mail="real@iteso.mx"), GROUPS_PATH: _groups()}
    )
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid, email="claim@iteso.mx"),
        access_token="at",
    )
    assert identity.email == "real@iteso.mx"


def test_entra_uses_upn_when_mail_is_empty(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, _ = _entra(
        public,
        {
            ME_PATH: _me(oid, mail=None, userPrincipalName="upn@iteso.mx"),
            GROUPS_PATH: _groups(),
        },
    )
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="at"
    )
    assert identity.email == "upn@iteso.mx"


def test_entra_follows_group_pages_inside_graph(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    responses = {
        ME_PATH: _me(oid),
        GROUPS_PATH: _groups("g1", next_link=f"{GRAPH_BASE}/next-page"),
        "/v1.0/next-page": _groups("g2"),
    }
    provider, _ = _entra(public, responses)
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="at"
    )
    assert identity.group_ids == ("g1", "g2")


def test_entra_never_follows_next_link_outside_graph(keypair: tuple) -> None:
    # Seguridad: el bearer token del usuario nunca se manda a otro dominio.
    private, public = keypair
    oid = str(uuid.uuid4())
    responses = {
        ME_PATH: _me(oid),
        GROUPS_PATH: _groups("g1", next_link="https://evil.example/steal"),
    }
    provider, transport = _entra(public, responses)
    provider.authenticate(id_token=make_id_token(private, oid=oid), access_token="at")
    assert all(r.url.host == "graph.microsoft.com" for r in transport.requests)


def test_entra_group_failure_keeps_profile_but_groups_none(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, _ = _entra(public, {ME_PATH: _me(oid), GROUPS_PATH: httpx.Response(403)})
    identity = provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="at"
    )
    assert identity.group_ids is None
    assert identity.directory_profile_available is True


def test_entra_graph_non_json_body_is_treated_as_down(keypair: tuple) -> None:
    private, public = keypair
    provider, _ = _entra(public, {ME_PATH: httpx.Response(200, content=b"<html>")})
    identity = provider.authenticate(id_token=make_id_token(private), access_token="at")
    assert identity.directory_profile_available is False


def test_entra_sends_bearer_token_to_graph(keypair: tuple) -> None:
    private, public = keypair
    oid = str(uuid.uuid4())
    provider, transport = _entra(public, {ME_PATH: _me(oid), GROUPS_PATH: _groups()})
    provider.authenticate(
        id_token=make_id_token(private, oid=oid), access_token="secreto"
    )
    assert transport.requests[0].headers["Authorization"] == "Bearer secreto"


def test_first_text_skips_blank_and_non_strings() -> None:
    assert _first_text(None, "  ", 5, " ana@iteso.mx ") == "ana@iteso.mx"
    assert _first_text(None, "") == ""


# ---------------------------------------------------------------------------
# Dominio institucional (BR-01)
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_EMAIL_DOMAIN="iteso.mx")
@pytest.mark.parametrize(
    ("email", "allowed"),
    [
        ("ana@iteso.mx", True),
        ("ANA@ITESO.MX", True),
        ("  ana@iteso.mx  ", True),
        ("ana@gmail.com", False),
        ("ana@sub.iteso.mx", False),
        ("ana@iteso.mx.evil.com", False),
        ("ana@iteso.mxx", False),
        ("@iteso.mx", False),
        ("iteso.mx", False),
        ("", False),
    ],
)
def test_is_institutional_email(email: str, allowed: bool) -> None:
    assert is_institutional_email(email) is allowed


# ---------------------------------------------------------------------------
# Rol inicial según el puesto
# ---------------------------------------------------------------------------


def _identity(job_title: str | None) -> ExternalIdentity:
    return ExternalIdentity(
        oid=str(uuid.uuid4()), tenant_id="t", email="x@iteso.mx", job_title=job_title
    )


@pytest.mark.parametrize(
    ("job_title", "role"),
    [
        ("Alumno", User.Role.STUDENT),
        ("ALUMNA", User.Role.STUDENT),
        ("estudiante", User.Role.STUDENT),
        ("Student", User.Role.STUDENT),
        ("Profesor de Asignatura", User.Role.STAFF),
        ("Docente", User.Role.STAFF),
        ("Coordinadora Académica", User.Role.STAFF),
        ("Director General", User.Role.STAFF),
        ("Personal Administrativo", User.Role.STAFF),
        ("Gerente de TI", User.Role.STAFF),
        ("", User.Role.STUDENT),
        (None, User.Role.STUDENT),
        ("Becario", User.Role.STUDENT),
    ],
)
def test_determine_initial_role_from_job_title(
    job_title: str | None, role: str
) -> None:
    assert determine_initial_role(_identity(job_title), "x@iteso.mx") == role


@override_settings(STAFF_EMAILS=["jefa@iteso.mx"])
def test_staff_emails_override_wins_over_job_title() -> None:
    assert (
        determine_initial_role(_identity("Alumno"), "JEFA@iteso.mx") == User.Role.STAFF
    )


def test_job_title_with_accents_is_normalized() -> None:
    assert (
        _normalize_job_title("  coordinadora   académica ") == "COORDINADORA ACADEMICA"
    )


def test_clip_trims_and_limits_length() -> None:
    assert _clip("  hola  ", 3) == "hol"
