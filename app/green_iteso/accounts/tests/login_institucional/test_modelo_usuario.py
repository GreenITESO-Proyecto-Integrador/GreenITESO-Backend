"""Login institucional: reglas del modelo User en las que se apoya el alta del usuario.

Referencia: ID01 ("Identidad 1" en la división de trabajo del equipo) = login
institucional con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o
actualización del usuario y rol según el puesto.

Caja blanca: se usan el manager y las restricciones de la base directamente.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction

from green_iteso.accounts.models import User


@pytest.mark.django_db
def test_create_user_pide_correo() -> None:
    with pytest.raises(ValueError):
        User.objects.create_user(email="")


@pytest.mark.django_db
def test_create_user_normaliza_el_dominio() -> None:
    # normalize_email de Django baja a minúsculas solo el dominio.
    user = User.objects.create_user(email="Ana@ITESO.MX")
    assert user.email == "Ana@iteso.mx"


@pytest.mark.django_db
def test_usuario_nuevo_es_student_por_defecto() -> None:
    assert User.objects.create_user(email="a@iteso.mx").role == User.Role.STUDENT


@pytest.mark.django_db
def test_create_superuser_queda_como_admin() -> None:
    admin = User.objects.create_superuser(email="root@iteso.mx", password="x")
    assert admin.role == User.Role.ADMIN
    assert admin.is_staff
    assert admin.is_superuser


@pytest.mark.django_db
def test_create_superuser_rechaza_is_staff_false() -> None:
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="r@iteso.mx", password="x", is_staff=False)


@pytest.mark.django_db
def test_create_superuser_rechaza_is_superuser_false() -> None:
    with pytest.raises(ValueError):
        User.objects.create_superuser(
            email="r@iteso.mx", password="x", is_superuser=False
        )


@pytest.mark.django_db
def test_rol_invalido_lo_bloquea_la_base() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="x@iteso.mx", role="ROOT")


@pytest.mark.django_db
def test_correo_repetido_lo_bloquea_la_base() -> None:
    User.objects.create_user(email="x@iteso.mx")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="x@iteso.mx")


@pytest.mark.django_db
def test_microsoft_oid_es_unico() -> None:
    oid = uuid.uuid4()
    User.objects.create_user(email="a@iteso.mx", microsoft_oid=oid)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="b@iteso.mx", microsoft_oid=oid)


@pytest.mark.django_db
def test_varios_usuarios_sin_oid_si_se_permiten() -> None:
    User.objects.create_user(email="a@iteso.mx")
    User.objects.create_user(email="b@iteso.mx")
    assert User.objects.filter(microsoft_oid__isnull=True).count() == 2


@pytest.mark.django_db
def test_el_id_es_uuid() -> None:
    assert isinstance(User.objects.create_user(email="a@iteso.mx").pk, uuid.UUID)


def test_no_hay_username_se_entra_por_correo() -> None:
    assert User.USERNAME_FIELD == "email"
    assert not User.REQUIRED_FIELDS
