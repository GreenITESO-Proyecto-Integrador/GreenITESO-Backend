"""Coverage for the /api/v1/actions/ catalog router.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-18
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from green_iteso.actions.models import ActionCategory, ActionMaster


@pytest.fixture(name="catalog")
def catalog_fixture() -> dict[str, ActionMaster]:
    """Two categories, one active action and one inactive action."""
    mobility = ActionCategory.objects.create(
        code="MOBILITY", name="Movilidad Verde", icon="bike"
    )
    ActionCategory.objects.create(code="ENERGY", name="Ahorro Energético")
    bike = ActionMaster.objects.create(
        code="BIKE",
        category=mobility,
        name="Uso de Bicicleta",
        description="Llega al campus en bici.",
        points=50,
        daily_limit=1,
        validation_type=ActionMaster.ValidationType.NONE,
        co2_kg_factor="1.250",
    )
    retired = ActionMaster.objects.create(
        code="RETIRED",
        category=mobility,
        name="Acción retirada",
        description="Ya no aplica.",
        points=5,
        validation_type=ActionMaster.ValidationType.PHOTO,
        is_active=False,
    )
    return {"bike": bike, "retired": retired}


@pytest.mark.django_db
def test_list_actions_is_public_and_returns_only_active(
    catalog: dict[str, ActionMaster],
) -> None:
    response = APIClient().get("/api/v1/actions/")

    assert response.status_code == 200
    results = response.json()["results"]
    assert [row["code"] for row in results] == ["BIKE"]

    bike = results[0]
    assert bike["id"] == str(catalog["bike"].id)
    assert bike["points"] == 50
    assert bike["daily_limit"] == 1
    assert bike["validation_type"] == "NONE"
    assert bike["co2_kg_factor"] == "1.250"
    assert bike["is_active"] is True
    assert bike["category"] == {
        "id": str(catalog["bike"].category_id),
        "code": "MOBILITY",
        "name": "Movilidad Verde",
        "description": "",
        "icon": "bike",
    }


@pytest.mark.django_db
def test_retrieve_action_returns_404_for_inactive(
    catalog: dict[str, ActionMaster],
) -> None:
    client = APIClient()

    active = client.get(f"/api/v1/actions/{catalog['bike'].id}/")
    inactive = client.get(f"/api/v1/actions/{catalog['retired'].id}/")

    assert active.status_code == 200
    assert active.json()["code"] == "BIKE"
    assert inactive.status_code == 404


@pytest.mark.django_db
@pytest.mark.usefixtures("catalog")
def test_list_categories_groups_active_actions() -> None:
    response = APIClient().get("/api/v1/actions/categories/")

    assert response.status_code == 200
    results = response.json()["results"]
    assert [row["code"] for row in results] == ["ENERGY", "MOBILITY"]

    energy, mobility = results
    assert energy["actions"] == []
    assert [a["code"] for a in mobility["actions"]] == ["BIKE"]
    assert "category" not in mobility["actions"][0]


@pytest.mark.django_db
@pytest.mark.usefixtures("catalog")
def test_catalog_is_read_only() -> None:
    client = APIClient()

    assert client.post("/api/v1/actions/", {"code": "X"}).status_code == 405
    assert client.post("/api/v1/actions/categories/", {}).status_code == 405
