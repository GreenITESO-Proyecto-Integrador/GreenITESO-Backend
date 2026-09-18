"""Coverage for actions catalog selectors.

Equipo: Equipo 1 - Acciones, Puntos y Gamificación
Última modificación: 2026-09-18
"""

from __future__ import annotations

import pytest

from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.actions.selectors import (
    list_active_actions,
    list_categories_with_active_actions,
)


def _make_action(
    category: ActionCategory, code: str, *, is_active: bool = True
) -> ActionMaster:
    return ActionMaster.objects.create(
        code=code,
        category=category,
        name=code.replace("_", " ").title(),
        description="test",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
        is_active=is_active,
    )


@pytest.mark.django_db
def test_list_active_actions_excludes_inactive_and_orders_by_category() -> None:
    recycling = ActionCategory.objects.create(code="RECYCLING", name="Reciclaje")
    mobility = ActionCategory.objects.create(code="MOBILITY", name="Movilidad")
    _make_action(recycling, "PET_BOTTLE")
    _make_action(mobility, "BIKE")
    _make_action(mobility, "OLD_ACTION", is_active=False)

    codes = list(list_active_actions().values_list("code", flat=True))

    assert codes == ["BIKE", "PET_BOTTLE"]


@pytest.mark.django_db
def test_list_categories_with_active_actions_prefetches_only_active() -> None:
    mobility = ActionCategory.objects.create(code="MOBILITY", name="Movilidad")
    empty = ActionCategory.objects.create(code="ENERGY", name="Energía")
    _make_action(mobility, "BIKE")
    _make_action(mobility, "OLD_ACTION", is_active=False)

    categories = list(list_categories_with_active_actions())

    by_code = {category.code: category for category in categories}
    assert set(by_code) == {"MOBILITY", "ENERGY"}
    assert [a.code for a in by_code["MOBILITY"].active_actions] == ["BIKE"]
    assert by_code[empty.code].active_actions == []
