"""Coverage for sharing a logged action into the social feed.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-25
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from green_iteso.accounts.models import Clan, User, UserProfile
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.actions.views import ActionLogCreateView
from green_iteso.feed.models import Post, PostType


@pytest.fixture(name="author")
def author_fixture() -> User:
    """User with the institutional clan and profile the log view expects."""
    user = User.objects.create_user(email="ana@iteso.mx", password="local-only")
    clan = Clan.objects.create(
        name="Ingeniería de Software", type=Clan.ClanType.INSTITUTIONAL
    )
    UserProfile.objects.create(user=user, institutional_clan=clan)
    return user


@pytest.fixture(name="bike")
def bike_fixture() -> ActionMaster:
    """Declarative action approved on registration."""
    category = ActionCategory.objects.create(code="MOBILITY", name="Movilidad Verde")
    return ActionMaster.objects.create(
        code="BIKE",
        category=category,
        name="Uso de Bicicleta",
        description="Llega al campus en bici.",
        points=50,
        validation_type=ActionMaster.ValidationType.NONE,
    )


def _post_log(author: User, payload: dict[str, object]) -> int:
    """Call ActionLogCreateView directly; the route is still pending."""
    request = APIRequestFactory().post("/api/v1/action-logs/", payload, format="json")
    force_authenticate(request, user=author)
    return ActionLogCreateView.as_view()(request).status_code


@pytest.mark.django_db
def test_log_without_flag_creates_no_post(author: User, bike: ActionMaster) -> None:
    status_code = _post_log(
        author, {"action_id": str(bike.id), "idempotency_key": uuid.uuid4().hex}
    )

    assert status_code == 201
    assert Post.objects.count() == 0
    assert ActionLog.objects.get().is_shared_publicly is False


@pytest.mark.django_db
def test_shared_log_publishes_the_action_to_the_feed(
    author: User, bike: ActionMaster
) -> None:
    status_code = _post_log(
        author,
        {
            "action_id": str(bike.id),
            "idempotency_key": uuid.uuid4().hex,
            "is_shared_publicly": True,
        },
    )

    assert status_code == 201
    assert ActionLog.objects.get().is_shared_publicly is True

    post = Post.objects.get()
    assert post.author == author
    assert post.post_type == PostType.SHARED_EVIDENCE
    assert post.content == "Nueva acción registrada: Uso de Bicicleta (+50 puntos)."
    assert post.image_url == ""


@pytest.mark.django_db
def test_shared_photo_log_does_not_publish_a_raw_object_key(author: User) -> None:
    category = ActionCategory.objects.create(code="RECYCLING", name="Reciclaje")
    thermos = ActionMaster.objects.create(
        code="THERMOS",
        category=category,
        name="Termo Reutilizable",
        description="Usa tu termo.",
        points=20,
        validation_type=ActionMaster.ValidationType.PHOTO,
    )

    status_code = _post_log(
        author,
        {
            "action_id": str(thermos.id),
            "idempotency_key": uuid.uuid4().hex,
            "evidence_object_key": "evidence/2026/thermos.jpg",
            "is_shared_publicly": True,
        },
    )

    assert status_code == 201
    assert ActionLog.objects.get().status == ActionLog.Status.PENDING_AUDIT

    post = Post.objects.get()
    assert post.image_url == ""
    assert "Termo Reutilizable" in post.content


@pytest.mark.django_db
def test_feed_failure_rolls_back_the_action_log(
    author: User, bike: ActionMaster, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unavailable(**_kwargs: object) -> Post:
        raise RuntimeError("feed unavailable")

    monkeypatch.setattr(
        "green_iteso.actions.views.create_shared_evidence_post", _unavailable
    )

    with pytest.raises(RuntimeError):
        _post_log(
            author,
            {
                "action_id": str(bike.id),
                "idempotency_key": uuid.uuid4().hex,
                "is_shared_publicly": True,
            },
        )

    assert ActionLog.objects.count() == 0
    assert Post.objects.count() == 0
