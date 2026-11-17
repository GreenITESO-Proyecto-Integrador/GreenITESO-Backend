from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "green_iteso.accounts"
    label = "accounts"

    def ready(self) -> None:
        # pylint: disable-next=import-outside-toplevel,unused-import
        from . import avatars  # noqa: F401
