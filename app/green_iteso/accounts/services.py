"""Write operations for the accounts domain."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import update_last_login
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework_simplejwt.tokens import RefreshToken

from .exceptions import (
    AccountConflictError,
    AccountDisabledError,
    CannotDemoteLastAdminError,
    DomainNotAllowedError,
    IdentityProviderUnavailableError,
    RequestValidationError,
)
from .identity import ExternalIdentity, IdentityProvider, get_identity_provider
from .models import User, UserProfile, UserRoleAudit

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


def determine_initial_role(identity: ExternalIdentity, email: str) -> str:
    """Assign STUDENT by default, or STAFF if configured or detected from directory data."""
    staff_emails = getattr(settings, "STAFF_EMAILS", [])
    if email.lower() in staff_emails:
        return User.Role.STAFF
    if identity.job_title and identity.job_title.strip():
        return User.Role.STAFF
    return User.Role.STUDENT


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
        if not identity.directory_profile_available:
            raise IdentityProviderUnavailableError()
        initial_role = determine_initial_role(identity, email)
        user = User(email=email, microsoft_oid=oid, role=initial_role)
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


def update_user_role(*, admin_user: User | None, user: User, new_role: str) -> User:
    """Change the global role of a user, enforcing admin rules and recording audit."""
    if new_role not in User.Role.values:
        raise RequestValidationError(f"Invalid role: {new_role}")

    with transaction.atomic():
        locked_users = list(
            User.objects.select_for_update()
            .filter(Q(role=User.Role.ADMIN, is_active=True) | Q(pk=user.pk))
            .order_by("pk")
        )
        locked_user = next(
            (locked_user for locked_user in locked_users if locked_user.pk == user.pk),
            None,
        )
        if locked_user is None:
            raise User.DoesNotExist

        if (
            locked_user.role == User.Role.ADMIN
            and new_role != User.Role.ADMIN
            and len(
                [
                    active_admin
                    for active_admin in locked_users
                    if active_admin.role == User.Role.ADMIN and active_admin.is_active
                ]
            )
            <= 1
        ):
            raise CannotDemoteLastAdminError()

        if locked_user.role == new_role:
            return locked_user

        previous_role = locked_user.role
        locked_user.role = new_role
        locked_user.save(update_fields=["role"])

        UserRoleAudit.objects.create(
            user=locked_user,
            changed_by=admin_user,
            previous_role=previous_role,
            new_role=new_role,
        )

        logger.info(
            "User role changed: target_user_id=%s previous_role=%s new_role=%s changed_by_id=%s",
            locked_user.pk,
            previous_role,
            new_role,
            admin_user.pk if admin_user is not None else None,
        )

    try:
        if admin_user is not None:
            change_msg = f"Role changed from {previous_role} to {new_role}"
            LogEntry.objects.log_actions(
                admin_user.pk,
                [locked_user],
                action_flag=CHANGE,
                change_message=change_msg,
            )
    except Exception:  # pragma: no cover
        logger.exception("Failed to write admin LogEntry for role change")

    return locked_user


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
