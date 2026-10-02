"""Management command to assign or promote a user's global role."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from green_iteso.accounts.exceptions import CannotDemoteLastAdminError
from green_iteso.accounts.models import User
from green_iteso.accounts.services import update_user_role


class Command(BaseCommand):
    help = "Assign or update the global role of a user by email."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("email", type=str, help="Email of the target user.")
        parser.add_argument(
            "role",
            type=str,
            choices=[r.value for r in User.Role],
            help="Role to assign: STUDENT, STAFF, or ADMIN.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = options["email"].strip().lower()
        role = options["role"].upper()

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"User with email '{email}' does not exist.")

        previous_role = user.role
        if previous_role == role:
            self.stdout.write(
                self.style.WARNING(f"User '{email}' already has role {role}.")
            )
            return

        try:
            update_user_role(admin_user=None, user=user, new_role=role)
        except CannotDemoteLastAdminError as exc:
            raise CommandError(str(exc.detail["error"]["message"])) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated role for '{email}': {previous_role} -> {role}."
            )
        )
