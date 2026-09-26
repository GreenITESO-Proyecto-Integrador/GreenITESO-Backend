"""Write operations for the accounts domain."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import IntegrityError, transaction
from rest_framework_simplejwt.tokens import RefreshToken

from .exceptions import (
    AccountConflictError,
    AccountDisabledError,
    DomainNotAllowedError,
)
from .identity import ExternalIdentity, IdentityProvider, get_identity_provider
from .models import User, UserProfile

logger = logging.getLogger(__name__)


def ensure_profile(user: User) -> UserProfile:
    """Return the user's profile, creating an empty one on first access."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def update_profile(
    *,
    user: User,
    bio: str | None = None,
    preferences: dict[str, object] | None = None,
    visibility: str | None = None,
    avatar_url: str | None = None,
) -> UserProfile:
    """Apply a partial edit to ``user``'s profile (T2-21).

    ``None`` means "not provided, leave unchanged", the same convention
    ``_sync_profile`` uses for Microsoft directory data -- an explicit empty
    string/dict is a real value (e.g. clearing the bio or avatar), not a
    no-op. Input validation (choices, URL shape, size limits) is the
    caller's (serializer's) job; this only decides what changed.
    """
    profile = ensure_profile(user)
    update_fields = []
    if bio is not None:
        profile.bio = bio
        update_fields.append("bio")
    if preferences is not None:
        profile.preferences = preferences
        update_fields.append("preferences")
    if visibility is not None:
        profile.visibility = visibility
        update_fields.append("visibility")
    if avatar_url is not None:
        profile.avatar_url = avatar_url
        update_fields.append("avatar_url")
    if update_fields:
        profile.save(update_fields=update_fields)
    return profile


@dataclass(frozen=True)
class LoginResult:
    user: User
    access: str
    refresh: str
    created: bool


def is_institutional_email(email: str) -> bool:
    """BR-01: the part after the last ``@`` must be exactly the allowed domain."""
    local, separator, domain = email.strip().lower().rpartition("@")
    return bool(separator and local) and domain == settings.ALLOWED_EMAIL_DOMAIN


def login_with_microsoft(
    *,
    id_token: str,
    access_token: str,
    provider: IdentityProvider | None = None,
) -> LoginResult:
    """Verify a Microsoft sign-in, create the user on first login, issue JWTs."""
    identity = (provider or get_identity_provider()).authenticate(
        id_token=id_token, access_token=access_token
    )
    email = identity.email.strip().lower()
    if not is_institutional_email(email):
        # Log neither the address nor the tokens; only that the gate fired.
        logger.info("Login rejected: email outside %s", settings.ALLOWED_EMAIL_DOMAIN)
        raise DomainNotAllowedError(
            f"Solo se permiten cuentas institucionales @{settings.ALLOWED_EMAIL_DOMAIN}."
        )

    user, created = _upsert_user(identity, email)
    update_last_login(None, user)
    refresh = RefreshToken.for_user(user)
    return LoginResult(
        user=user,
        access=str(refresh.access_token),
        refresh=str(refresh),
        created=created,
    )


def _upsert_user(identity: ExternalIdentity, email: str) -> tuple[User, bool]:
    # Two first logins can race on the unique oid/email; the loser retries and
    # finds the winner's row.
    for attempt in range(2):
        try:
            with transaction.atomic():
                return _get_or_create_user(identity, email)
        except IntegrityError:
            if attempt == 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _get_or_create_user(identity: ExternalIdentity, email: str) -> tuple[User, bool]:
    oid = uuid.UUID(identity.oid)
    user = User.objects.select_for_update().filter(microsoft_oid=oid).first()
    created = False
    if user is None:
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user is not None:
            if user.microsoft_oid is not None:
                raise AccountConflictError()
            user.microsoft_oid = oid
    if user is None:
        user = User(email=email, microsoft_oid=oid)
        user.set_unusable_password()
        created = True
    if not user.is_active:
        raise AccountDisabledError()

    if not created and user.email != email:
        taken = User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()
        if not taken:
            user.email = email
    if identity.given_name is not None:
        user.first_name = _clip(identity.given_name, 150)
    if identity.surname is not None:
        user.last_name = _clip(identity.surname, 150)
    user.save()

    _sync_profile(user, identity)
    return user, created


def _sync_profile(user: User, identity: ExternalIdentity) -> None:
    """Store the Microsoft directory data; ``None`` keeps what was stored."""
    profile = ensure_profile(user)
    if identity.job_title is not None:
        profile.job_title = _clip(identity.job_title, 255)
    if identity.department is not None:
        profile.department = _clip(identity.department, 255)
    if identity.employee_id is not None:
        profile.employee_id = _clip(identity.employee_id, 64)
    if identity.group_ids is not None:
        profile.microsoft_group_ids = list(identity.group_ids)
    profile.save()


def _clip(value: str, max_length: int) -> str:
    return value.strip()[:max_length]
