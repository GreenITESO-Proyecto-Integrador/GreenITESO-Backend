"""Management command to assign or promote a user's global role."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from green_iteso.accounts.models import User, UserRoleAudit


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

        user.role = role
        user.save(update_fields=["role"])

        UserRoleAudit.objects.create(
            user=user,
            changed_by=None,
            previous_role=previous_role,
            new_role=role,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated role for '{email}': {previous_role} -> {role}."
            )
        )
