from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "green_iteso.feedback"
    label = "feedback"

    def ready(self) -> None:
        # pylint: disable-next=import-outside-toplevel,unused-import
        from . import services  # noqa: F401
