"""Read-only queries for the accounts domain."""

from __future__ import annotations

import uuid

from .models import User


def get_user_by_id(user_id: uuid.UUID) -> User:
    """Return the account identified by ``user_id``."""
    return User.objects.get(pk=user_id)
