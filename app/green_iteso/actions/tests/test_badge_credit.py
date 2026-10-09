"""Badge thresholds use points credited by submission and audit."""

import pytest

from green_iteso.accounts.models import UserProfile
from green_iteso.actions.models import ActionMaster
from green_iteso.gamification.models import Badge, UserBadge
from green_iteso.notifications.models import Notification

from .helpers import (
    audit_action_log,
    create_admin,
    create_bike_action,
    create_student,
    post_action_log,
)


@pytest.mark.django_db
@pytest.mark.parametrize("requires_audit", [False, True])
def test_credit_awards_badge_at_new_points_threshold(requires_audit: bool) -> None:
    user = create_student()
    UserProfile.objects.filter(user=user).update(total_points=90, available_points=90)
    action = create_bike_action(points=10)
    if requires_audit:
        action.validation_type = ActionMaster.ValidationType.PHOTO
        action.save(update_fields=["validation_type"])
    badge = Badge.objects.create(name="100 points", points_required=100)

    response = post_action_log(
        user, action, "evidence/photo.jpg" if requires_audit else ""
    )
    assert response.status_code == 201
    if requires_audit:
        assert not UserBadge.objects.filter(user=user, badge=badge).exists()
        response = audit_action_log(
            create_admin(), response.json()["log_id"], "APPROVED"
        )
        assert response.status_code == 200

    profile = UserProfile.objects.get(user=user)
    assert (profile.total_points, profile.available_points) == (100, 100)
    assert UserBadge.objects.filter(user=user, badge=badge).count() == 1
    assert (
        Notification.objects.filter(
            user=user, notification_type=Notification.NotificationType.BADGE_EARNED
        ).count()
        == 1
    )
