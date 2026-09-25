"""Coverage for the actions selectors."""

from __future__ import annotations

import pytest

from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.actions.selectors import (
    list_active_action_categories,
    list_active_actions,
)


def _make_action(
    category: ActionCategory, code: str, *, is_active: bool = True
) -> ActionMaster:
    return ActionMaster.objects.create(
        code=code,
        category=category,
        name=code.replace("_", " ").title(),
        description="Test action",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
        is_active=is_active,
    )


@pytest.mark.django_db
def test_list_active_action_categories_is_ordered_by_code() -> None:
    ActionCategory.objects.create(code="WASTE", name="Residuos")
    ActionCategory.objects.create(code="MOBILITY", name="Movilidad Verde")

    codes = list(list_active_action_categories().values_list("code", flat=True))

    assert codes == ["MOBILITY", "WASTE"]


@pytest.mark.django_db
def test_list_active_actions_excludes_inactive_and_is_ordered_by_code() -> None:
    mobility = ActionCategory.objects.create(code="MOBILITY", name="Movilidad Verde")
    _make_action(mobility, "WALK")
    _make_action(mobility, "BIKE")
    _make_action(mobility, "RETIRED", is_active=False)

    codes = list(list_active_actions().values_list("code", flat=True))

    assert codes == ["BIKE", "WALK"]
