"""API tests for feedback submissions."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.core.tests.image_helpers import (
    PNG_BYTES,
    use_local_media,
)
from green_iteso.core.tests.image_helpers import png_upload as _png
from green_iteso.feedback.models import FeedbackSubmission

FEEDBACK_URL = "/api/v1/feedback/"
MINE_URL = "/api/v1/feedback/mine/"


@pytest.fixture
def feedback_media(settings: Any, tmp_path: Path) -> Path:
    return use_local_media(settings, tmp_path)


@pytest.fixture
def student() -> User:
    return User.objects.create_user(
        email="estudiante@iteso.mx",
        first_name="Estudiante",
        role=User.Role.STUDENT,
    )


@pytest.fixture
def other_student() -> User:
    return User.objects.create_user(
        email="otro@iteso.mx",
        first_name="Otro",
        role=User.Role.STUDENT,
    )


@pytest.fixture
def admin() -> User:
    return User.objects.create_user(
        email="admin@iteso.mx",
        first_name="Admin",
        role=User.Role.ADMIN,
    )


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_submission_requires_authentication() -> None:
    response = APIClient().post(
        FEEDBACK_URL,
        {"category": "IDEA", "subject": "Test", "message": "Mensaje de prueba valido"},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_create_valid_feedback(student: User) -> None:
    client = _client(student)
    response = client.post(
        FEEDBACK_URL,
        {
            "category": FeedbackSubmission.Category.IDEA,
            "rating": 5,
            "subject": "Propuesta de reciclaje de pilas",
            "message": "Sería genial contar con contenedores para pilas en el edificio T.",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["subject"] == "Propuesta de reciclaje de pilas"
    assert data["category"] == FeedbackSubmission.Category.IDEA
    assert data["rating"] == 5
    assert data["status"] == FeedbackSubmission.Status.OPEN
    assert data["has_screenshot"] is False
    assert FeedbackSubmission.objects.filter(user=student).count() == 1


@pytest.mark.django_db
def test_create_feedback_validation_errors(student: User) -> None:
    client = _client(student)

    # Empty subject
    res1 = client.post(
        FEEDBACK_URL,
        {
            "category": "BUG",
            "subject": "   ",
            "message": "Mensaje largo de prueba valido",
        },
    )
    assert res1.status_code == 400
    assert "subject" in res1.json()

    # Short message (< 10 chars)
    res2 = client.post(
        FEEDBACK_URL,
        {"category": "BUG", "subject": "Error en boton", "message": "Corto"},
    )
    assert res2.status_code == 400
    assert "message" in res2.json()

    # Invalid rating (> 5)
    res3 = client.post(
        FEEDBACK_URL,
        {
            "category": "BUG",
            "subject": "Error en boton",
            "message": "Mensaje largo de prueba valido",
            "rating": 6,
        },
    )
    assert res3.status_code == 400
    assert "rating" in res3.json()


@pytest.mark.django_db
def test_create_feedback_with_screenshot(student: User, feedback_media: Path) -> None:
    client = _client(student)
    response = client.post(
        FEEDBACK_URL,
        {
            "category": FeedbackSubmission.Category.BUG,
            "subject": "Captura del problema en móvil",
            "message": "Adjunto la captura donde se desborda el botón en pantalla pequeña.",
            "screenshot": _png(),
        },
        format="multipart",
    )

    assert response.status_code == 201
    data = response.json()
    assert data["has_screenshot"] is True

    submission = FeedbackSubmission.objects.get(pk=data["id"])
    assert submission.screenshot_object_key.startswith(f"feedback/{student.pk}/")
    assert (feedback_media / submission.screenshot_object_key).read_bytes() == PNG_BYTES


@pytest.mark.django_db
def test_my_feedback_list_shows_only_own_submissions(
    student: User, other_student: User
) -> None:
    FeedbackSubmission.objects.create(
        user=student,
        category=FeedbackSubmission.Category.IDEA,
        subject="Mi sugerencia",
        message="Contenido de mi sugerencia suficientemente largo.",
    )
    FeedbackSubmission.objects.create(
        user=other_student,
        category=FeedbackSubmission.Category.BUG,
        subject="Sugerencia ajena",
        message="Contenido ajeno suficientemente largo para validar.",
    )

    client = _client(student)
    response = client.get(MINE_URL)

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["subject"] == "Mi sugerencia"


@pytest.mark.django_db
def test_admin_can_list_and_filter_all_feedback(
    admin: User, student: User, other_student: User
) -> None:
    FeedbackSubmission.objects.create(
        user=student,
        category=FeedbackSubmission.Category.BUG,
        subject="Bug reportado",
        message="Mensaje del bug suficientemente largo.",
        status=FeedbackSubmission.Status.OPEN,
    )
    FeedbackSubmission.objects.create(
        user=other_student,
        category=FeedbackSubmission.Category.IDEA,
        subject="Idea reportada",
        message="Mensaje de la idea suficientemente largo.",
        status=FeedbackSubmission.Status.RESOLVED,
    )

    student_client = _client(student)
    assert student_client.get(FEEDBACK_URL).status_code == 403

    admin_client = _client(admin)
    all_res = admin_client.get(FEEDBACK_URL)
    assert all_res.status_code == 200
    assert all_res.json()["count"] == 2

    # Filter by category
    bug_res = admin_client.get(f"{FEEDBACK_URL}?category=BUG")
    assert bug_res.status_code == 200
    assert bug_res.json()["count"] == 1
    assert bug_res.json()["results"][0]["subject"] == "Bug reportado"

    # Filter by status
    resolved_res = admin_client.get(f"{FEEDBACK_URL}?status=RESOLVED")
    assert resolved_res.status_code == 200
    assert resolved_res.json()["count"] == 1
    assert resolved_res.json()["results"][0]["subject"] == "Idea reportada"


@pytest.mark.django_db
def test_admin_can_update_status_and_notes(admin: User, student: User) -> None:
    submission = FeedbackSubmission.objects.create(
        user=student,
        category=FeedbackSubmission.Category.BUG,
        subject="Problema en tienda",
        message="No se descuentan los puntos al intentar canjear un marco.",
    )

    admin_client = _client(admin)
    patch_url = f"{FEEDBACK_URL}{submission.pk}/"

    response = admin_client.patch(
        patch_url,
        {
            "status": FeedbackSubmission.Status.RESOLVED,
            "admin_notes": "Corregido en el despliegue v1.2.",
        },
        format="json",
    )

    assert response.status_code == 200
    submission.refresh_from_db()
    assert submission.status == FeedbackSubmission.Status.RESOLVED
    assert submission.admin_notes == "Corregido en el despliegue v1.2."


@pytest.mark.django_db
@pytest.mark.usefixtures("feedback_media")
def test_screenshot_access_permissions(
    student: User, other_student: User, admin: User
) -> None:
    client = _client(student)
    create_res = client.post(
        FEEDBACK_URL,
        {
            "category": FeedbackSubmission.Category.BUG,
            "subject": "Captura de error",
            "message": "Se adjunta la captura correspondiente del fallo.",
            "screenshot": _png(),
        },
        format="multipart",
    )
    submission_id = create_res.json()["id"]
    screenshot_url = f"{FEEDBACK_URL}{submission_id}/screenshot/"

    # Author can view
    author_res = client.get(screenshot_url)
    assert author_res.status_code == 200
    assert author_res["Content-Type"] == "image/png"

    # Admin can view
    admin_res = _client(admin).get(screenshot_url)
    assert admin_res.status_code == 200

    # Other student is forbidden
    other_res = _client(other_student).get(screenshot_url)
    assert other_res.status_code == 403
