"""Services for gamification and reward logic."""

from django.db import transaction

from green_iteso.accounts.models import User
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import notify

from .models import Badge, UserBadge


def check_and_award_badges(user: User) -> None:
    """Check user points and award eligible badges safely against concurrency."""
    total_points = user.profile.total_points

    earned_badge_ids = set(user.badges.values_list("badge_id", flat=True))

    eligible_badges = list(
        Badge.objects.filter(is_active=True, points_required__lte=total_points).exclude(
            id__in=earned_badge_ids
        )
    )

    if not eligible_badges:
        return

    with transaction.atomic():
        for badge in eligible_badges:
            created = UserBadge.objects.get_or_create(user=user, badge=badge)[1]
            if created:
                # notify() saves the notification and pushes it over the
                # WebSocket after commit, so the frontend can celebrate it live.
                notify(
                    user,
                    Notification.NotificationType.BADGE_EARNED,
                    badge_name=badge.name,
                )
