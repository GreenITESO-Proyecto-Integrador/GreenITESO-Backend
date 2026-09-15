"""Write operations for the accounts domain."""

from __future__ import annotations

from .models import User, UserProfile


def ensure_profile(user: User) -> UserProfile:
    """Return the user's profile, creating an empty one on first access."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile
