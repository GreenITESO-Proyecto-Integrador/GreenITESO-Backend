"""Services for gamification and reward logic."""

from django.db import transaction

from green_iteso.accounts.models import User
from green_iteso.notifications.models import Notification

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
                Notification.objects.create(
                    user=user,
                    title="¡Nueva medalla desbloqueada!",
                    message=f"Has obtenido el logro: {badge.name}. ¡Sigue así!",
                    notification_type=Notification.NotificationType.BADGE_EARNED,
                )
