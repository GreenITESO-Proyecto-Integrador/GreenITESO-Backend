"""Login institucional: problemas que encontraron las pruebas.

Referencia: ID01 ("Identidad 1" en la división de trabajo del equipo) = login
institucional con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o
actualización del usuario y rol según el puesto.

Cada prueba afirma cómo DEBERÍA comportarse el sistema y va marcada con
``xfail(strict=True)``: hoy falla porque el problema existe. Cuando se arregle,
pytest avisa y el reporte de ``conftest.py`` lo marca como resuelto.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.identity.entra import GRAPH_BASE, EntraProvider
from green_iteso.accounts.models import User
from green_iteso.accounts.services import (
    determine_initial_role,
    is_institutional_email,
    update_user_role,
)
from green_iteso.accounts.tests.identity_helpers import (
    CLIENT_ID,
    TENANT_ID,
    RecordingTransport,
    StubKeyResolver,
    make_id_token,
    make_keypair,
)

LOGIN_URL = "/api/v1/auth/login/"


def _login(**claims: object) -> None:
    response = APIClient().post(
        LOGIN_URL,
        {"id_token": "mock:" + json.dumps(claims), "access_token": ""},
        format="json",
    )
    assert response.status_code == 200, response.content


@pytest.mark.hallazgo(
    titulo="El login le quita el rol STAFF que asignó un administrador",
    integrante="DehBarca",
    severidad="alta",
    detalle=(
        "Un admin sube a STAFF a un alumno (PATCH /users/{id}/role/). En su siguiente "
        "login, el puesto 'Alumno' del directorio lo regresa a STUDENT. Solo el rol "
        "ADMIN está protegido en _get_or_create_user."
    ),
    recomendacion=(
        "Si el último cambio de rol lo hizo un admin (hay un UserRoleAudit), no "
        "recalcular el rol en el login; o guardar un campo 'rol fijado por admin'."
    ),
)
@pytest.mark.xfail(strict=True, reason="El login sobrescribe el rol del admin")
@pytest.mark.django_db
@pytest.mark.usefixtures("modo_mock")
def test_rol_asignado_por_admin_sobrevive_al_login() -> None:
    _login(email="ana@iteso.mx", job_title="Alumno")
    admin = User.objects.create_user(email="admin@iteso.mx", role=User.Role.ADMIN)
    ana = User.objects.get(email="ana@iteso.mx")
    update_user_role(admin_user=admin, user=ana, new_role=User.Role.STAFF)

    _login(email="ana@iteso.mx", job_title="Alumno")

    ana.refresh_from_db()
    assert ana.role == User.Role.STAFF


@pytest.mark.hallazgo(
    titulo="Un alumno con 'Profesor' en el puesto se vuelve STAFF",
    integrante="DehBarca",
    severidad="media",
    detalle=(
        "determine_initial_role busca palabras dentro del puesto. 'Alumno Asistente "
        "de Profesor' contiene PROFESOR y queda como STAFF aunque es alumno."
    ),
    recomendacion=(
        "Revisar primero si el puesto empieza con ALUMNO/ESTUDIANTE antes de "
        "buscar palabras de personal, o usar los grupos de Entra en vez del texto."
    ),
)
@pytest.mark.xfail(strict=True, reason="La regla de rol usa coincidencia de texto")
def test_alumno_asistente_de_profesor_es_student() -> None:
    identity = ExternalIdentity(
        oid=str(uuid.uuid4()),
        tenant_id="t",
        email="ana@iteso.mx",
        job_title="Alumno Asistente de Profesor",
    )
    assert determine_initial_role(identity, "ana@iteso.mx") == User.Role.STUDENT


@pytest.mark.hallazgo(
    titulo="La base de datos acepta el mismo correo con distintas mayúsculas",
    integrante="Pardosito",
    severidad="media",
    detalle=(
        "User.email es único pero distingue mayúsculas: 'X@iteso.mx' y 'x@iteso.mx' "
        "pueden ser dos cuentas. El login normaliza a minúsculas, pero el admin de "
        "Django, un seed o un script pueden crear el duplicado."
    ),
    recomendacion=(
        "Agregar un UniqueConstraint sobre Lower('email') con su migración, y "
        "normalizar el correo en UserManager.create_user."
    ),
)
@pytest.mark.xfail(strict=True, reason="El índice único distingue mayúsculas")
@pytest.mark.django_db
def test_correo_unico_sin_importar_mayusculas() -> None:
    User.objects.create_user(email="ana@iteso.mx")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="ANA@iteso.mx")


@pytest.mark.hallazgo(
    titulo="Los grupos de Microsoft se cortan en silencio después de 3 páginas",
    integrante="Pardosito",
    severidad="baja",
    detalle=(
        "EntraProvider._fetch_group_ids lee como máximo 3 páginas de Graph. Si hay "
        "más, guarda una lista incompleta como si fuera la lista completa."
    ),
    recomendacion=(
        "Si queda un @odata.nextLink después del límite, devolver None ('no se "
        "sabe') para no sobrescribir los grupos guardados, y registrar un aviso."
    ),
)
@pytest.mark.xfail(strict=True, reason="Se guardan grupos incompletos")
def test_grupos_incompletos_no_se_guardan_como_completos() -> None:
    private_key, public_key = make_keypair()
    oid = str(uuid.uuid4())
    groups_path = "/v1.0/me/transitiveMemberOf/microsoft.graph.group"
    endless_page = httpx.Response(
        200,
        json={
            "value": [{"id": "g"}],
            "@odata.nextLink": f"{GRAPH_BASE}/me/transitiveMemberOf/microsoft.graph.group",
        },
    )
    transport = RecordingTransport(
        {
            "/v1.0/me": httpx.Response(200, json={"id": oid, "mail": "a@iteso.mx"}),
            groups_path: endless_page,
        }
    )
    provider = EntraProvider(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        key_resolver=StubKeyResolver(public_key),
        http_client=httpx.Client(transport=transport),
    )

    identity = provider.authenticate(
        id_token=make_id_token(private_key, oid=oid), access_token="at"
    )

    assert identity.group_ids is None


@pytest.mark.hallazgo(
    titulo="El filtro de dominio acepta correos con dos @",
    integrante="Pardosito",
    severidad="baja",
    detalle=(
        "is_institutional_email solo mira lo que va después de la última @, así que "
        "'a@gmail.com@iteso.mx' pasa. Entra no emite correos así, pero el filtro por "
        "sí solo no lo bloquea."
    ),
    recomendacion="Rechazar correos con más de una @ (email.count('@') != 1).",
)
@pytest.mark.xfail(strict=True, reason="Solo se revisa la última @")
@override_settings(ALLOWED_EMAIL_DOMAIN="iteso.mx")
def test_correo_con_dos_arrobas_no_es_institucional() -> None:
    assert is_institutional_email("a@gmail.com@iteso.mx") is False
