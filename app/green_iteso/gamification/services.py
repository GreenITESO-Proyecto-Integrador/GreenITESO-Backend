"""Services for gamification and reward logic."""

from django.db import transaction

from green_iteso.accounts.models import User
from green_iteso.notifications.models import Notification

from .models import Badge, UserBadge


def check_and_award_badges(user: User) -> None:
    """Check user points and award eligible badges, triggering notifications."""
    total_points = user.profile.total_points

    # Buscar medallas que el usuario ya tiene para no duplicarlas
    earned_badge_ids = set(user.badges.values_list("badge_id", flat=True))

    # Filtrar medallas activas que pidan menos o igual puntos y no se hayan ganado
    eligible_badges = list(
        Badge.objects.filter(is_active=True, points_required__lte=total_points).exclude(
            id__in=earned_badge_ids
        )
    )

    if not eligible_badges:
        return

    with transaction.atomic():
        # Asignar las nuevas medallas al usuario
        UserBadge.objects.bulk_create(
            [UserBadge(user=user, badge=badge) for badge in eligible_badges]
        )

        # Disparar la notificacion BADGE_EARNED por cada medalla nueva
        Notification.objects.bulk_create(
            [
                Notification(
                    user=user,
                    title="¡Nueva medalla desbloqueada!",
                    message=f"Has obtenido el logro: {badge.name}. ¡Sigue así!",
                    notification_type=Notification.NotificationType.BADGE_EARNED,
                )
                for badge in eligible_badges
            ]
        )
