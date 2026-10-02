from django.apps import AppConfig
from django.core.checks import register


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "green_iteso.notifications"
    label = "notifications"

    def ready(self) -> None:
        # pylint: disable-next=import-outside-toplevel
        from .checks import check_channel_layer_matches_workers

        register(check_channel_layer_matches_workers)
