"""Invalid audit requests must leave pending evidence and balances unchanged."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import UserProfile
from green_iteso.actions.models import ActionLog, ActionMaster
from green_iteso.notifications.models import Notification

from .helpers import create_admin, create_bike_action, create_student, post_action_log


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{}, {"status": "UNKNOWN"}, {"status": "REJECTED"}])
def test_invalid_audit_preserves_pending_log(payload: dict[str, Any]) -> None:
    user = create_student()
    admin = create_admin()
    action = create_bike_action()
    action.validation_type = ActionMaster.ValidationType.PHOTO
    action.save(update_fields=["validation_type"])
    created = post_action_log(user, action, "evidence/photo.jpg")
    assert created.status_code == 201
    log = ActionLog.objects.get(pk=created.json()["log_id"])
    client = APIClient()
    client.force_authenticate(admin)

    response = client.patch(
        f"/api/v1/action-logs/{log.pk}/audit/", payload, format="json"
    )

    assert response.status_code == 400
    log.refresh_from_db()
    assert log.status == ActionLog.Status.PENDING_AUDIT
    assert log.reviewed_by_id is None
    assert log.reviewed_at is None
    profile = UserProfile.objects.get(user=user)
    assert profile.total_points == profile.available_points == 0
    assert Notification.objects.filter(user=user).count() == 0
