"""App config for the clans domain."""

from django.apps import AppConfig


class ClansConfig(AppConfig):
    """Django app configuration for the clans domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "green_iteso.clans"
    label = "clans"
