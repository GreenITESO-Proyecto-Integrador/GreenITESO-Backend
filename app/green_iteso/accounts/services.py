"""Write operations for the accounts domain."""

from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import update_last_login
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from green_iteso.actions.evidence import delete_action_evidence
from green_iteso.actions.models import ActionLog
from green_iteso.campaigns.models import CampaignParticipant, UserMissionProgress
from green_iteso.campaigns.services import enroll_in_intro_campaign
from green_iteso.feed.models import Post
from green_iteso.gamification.models import UserBadge
from green_iteso.notifications.models import Notification
from green_iteso.notifications.services import close_user_sockets

from .avatars import delete_profile_avatar, save_profile_avatar
from .exceptions import (
    AccountConflictError,
    AccountDisabledError,
    CannotDemoteLastAdminError,
    DomainNotAllowedError,
    IdentityProviderUnavailableError,
    RequestValidationError,
)
from .identity import ExternalIdentity, IdentityProvider, get_identity_provider
from .models import ClanMembership, User, UserProfile, UserRoleAudit

logger = logging.getLogger(__name__)

# Points charged for every nickname change after the first, which is free (#175).
NICKNAME_CHANGE_COST_POINTS = 50


def ensure_profile(user: User) -> UserProfile:
    """Return the user's profile, creating an empty one on first access."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def ensure_account_writable(
    user: User, *, requested_at: datetime | None = None
) -> None:
    """Reject a write queued behind a deactivation (#176).

    Call while holding the user row lock, so the check sees a deactivation
    that has already committed. When ``requested_at`` is given, a request that
    started before the profile's progress was last reset is rejected too: it
    was made for the account's old state.

    Raises:
        AccountDisabledError: If the account is inactive or was reset after
            ``requested_at``.
    """
    if not user.is_active:
        raise AccountDisabledError()
    if requested_at is None:
        return
    reset_at = (
        UserProfile.objects.filter(user_id=user.pk)
        .values_list("progress_reset_at", flat=True)
        .first()
    )
    if reset_at is not None and reset_at > requested_at:
        raise AccountDisabledError()


@transaction.atomic
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
    # Lock the user row first, as the other profile writers do, so a
    # deactivation that commits first is seen here.
    locked = User.objects.select_for_update(no_key=True).get(pk=user.pk)
    ensure_account_writable(locked)
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
    previous_key = ""
    if avatar_url is not None:
        # A pasted link replaces an uploaded photo, so only one source is live.
        previous_key = profile.avatar_object_key
        profile.avatar_url = avatar_url
        profile.avatar_object_key = ""
        update_fields += ["avatar_url", "avatar_object_key"]
    if update_fields:
        profile.save(update_fields=update_fields)
    if previous_key:
        user_id = user.pk
        transaction.on_commit(lambda: delete_profile_avatar(user_id, previous_key))
    return profile


@transaction.atomic
def set_profile_avatar(*, user: User, uploaded: UploadedFile | None) -> UserProfile:
    """Replace, or with ``uploaded=None`` remove, ``user``'s profile photo (#170).

    An upload also clears any pasted ``avatar_url``. The new key is generated
    by ``avatars.save_profile_avatar`` and is scoped to this user. The
    previous file is deleted only once the change commits, so a rolled-back
    request never loses the current photo.

    Raises:
        InvalidImageError: If the upload is not a JPG/PNG/WEBP of at most 5 MB.
    """
    locked = User.objects.select_for_update(no_key=True).get(pk=user.pk)
    ensure_account_writable(locked)
    ensure_profile(user)
    profile = UserProfile.objects.select_for_update().get(user=user)
    previous_key = profile.avatar_object_key
    new_key = save_profile_avatar(user.pk, uploaded) if uploaded is not None else ""

    try:
        profile.avatar_object_key = new_key
        profile.avatar_url = ""
        profile.save(update_fields=["avatar_object_key", "avatar_url"])
    except Exception:
        if new_key:
            delete_profile_avatar(user.pk, new_key)
        raise
    if previous_key and previous_key != new_key:
        user_id = user.pk
        transaction.on_commit(lambda: delete_profile_avatar(user_id, previous_key))
    return profile


class NicknameUnchangedError(ValueError):
    """Raised when the requested nickname is the one the user already has."""


class InsufficientPointsForNicknameError(ValueError):
    """Raised when ``available_points`` cannot cover a nickname change."""


@dataclass(frozen=True)
class NicknameChange:
    """Outcome of ``change_nickname``, enough for a client to refresh its balance."""

    nickname: str
    points_spent: int
    available_points: int
    next_change_cost: int


def nickname_change_cost(profile: UserProfile) -> int:
    """Return what the user's next nickname change costs: 0 the first time."""
    return 0 if profile.nickname_changes == 0 else NICKNAME_CHANGE_COST_POINTS


@transaction.atomic
def change_nickname(*, user: User, nickname: str) -> NicknameChange:
    """Set ``user``'s nickname; the first change is free, later ones cost points (#175).

    Locks the user before the profile, matching onboarding's lock order, so
    overlapping onboarding and nickname changes cannot deadlock.
    Locks the profile row so two concurrent changes cannot both pass the
    balance check or both count as "the first". Points come out of
    ``available_points`` only: ``total_points`` (ranking) is untouched, the
    same as buying from the store. The caller validates the nickname's shape.

    Raises:
        NicknameUnchangedError: If ``nickname`` is already the user's nickname;
            nothing is charged.
        InsufficientPointsForNicknameError: If the balance is below the cost.
    """
    # A NO KEY UPDATE lock preserves the user-before-profile ordering shared
    # with onboarding without blocking notification FK checks (KEY SHARE).
    locked = User.objects.select_for_update(no_key=True).get(pk=user.pk)
    ensure_account_writable(locked)
    profile = UserProfile.objects.select_for_update().get(pk=ensure_profile(user).pk)
    user.refresh_from_db(fields=["nickname"])
    if nickname == user.nickname:
        raise NicknameUnchangedError("That is already your nickname.")

    cost = nickname_change_cost(profile)
    if profile.available_points < cost:
        raise InsufficientPointsForNicknameError(
            f"Changing your nickname costs {cost} points "
            f"and you have {profile.available_points}."
        )

    user.nickname = nickname
    user.save(update_fields=["nickname"])
    profile.available_points -= cost
    profile.nickname_changes += 1
    profile.save(update_fields=["available_points", "nickname_changes"])
    return NicknameChange(
        nickname=nickname,
        points_spent=cost,
        available_points=profile.available_points,
        next_change_cost=nickname_change_cost(profile),
    )


@dataclass(frozen=True)
class LoginResult:
    user: User
    access: str
    refresh: str
    created: bool
    reactivated: bool = False


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

    user, created, reactivated = _upsert_user(identity, email)
    if created:
        enroll_in_intro_campaign(user)
    update_last_login(None, user)
    refresh = RefreshToken.for_user(user)
    return LoginResult(
        user=user,
        access=str(refresh.access_token),
        refresh=str(refresh),
        created=created,
        reactivated=reactivated,
    )


def _upsert_user(identity: ExternalIdentity, email: str) -> tuple[User, bool, bool]:
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
    """Infer the application role from the directory title and exceptions."""
    staff_emails = {
        configured_email.strip().lower()
        for configured_email in getattr(settings, "STAFF_EMAILS", [])
    }
    if email.strip().lower() in staff_emails:
        return User.Role.STAFF

    normalized_title = _normalize_job_title(identity.job_title)
    if normalized_title in {
        "ALUMNO",
        "ALUMNA",
        "ALUMNADO",
        "ESTUDIANTE",
        "STUDENT",
    }:
        return User.Role.STUDENT
    if any(
        marker in normalized_title
        for marker in (
            "PROFESOR",
            "DOCENTE",
            "TEACHER",
            "STAFF",
            "PERSONAL",
            "ADMINISTRAT",
            "DIRECTOR",
            "COORDINADOR",
            "COORDINADORA",
            "GERENTE",
            "MANAGER",
        )
    ):
        return User.Role.STAFF
    return User.Role.STUDENT


def _normalize_job_title(job_title: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", job_title or "")
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(without_accents.upper().split())


def _reactivate_if_self_deactivated(user: User) -> bool:
    """Bring a self-deactivated account back; return whether it was one (#176).

    Only an account the user deactivated themselves may come back; one an admin
    disabled stays locked. Its progress was already wiped when it was
    deactivated, so signing in again is a fresh start.
    """
    if user.is_active:
        return False
    if user.deactivated_at is None:
        raise AccountDisabledError()
    user.is_active = True
    user.deactivated_at = None
    return True


def _get_or_create_user(
    identity: ExternalIdentity, email: str
) -> tuple[User, bool, bool]:
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
    reactivated = _reactivate_if_self_deactivated(user)

    # Reconcile the role from directory data on every login. Preserve ADMIN
    # because it is an application-managed role; Django's is_staff/is_superuser
    # flags are intentionally not part of this update. Skip when the directory
    # was not read (Graph down or no profile): None means "unknown", not "empty",
    # and must not demote an existing STAFF account to STUDENT.
    directory_title_known = (
        identity.directory_profile_available and identity.job_title is not None
    )
    if not created and user.role != User.Role.ADMIN and directory_title_known:
        login_role = determine_initial_role(identity, email)
        if user.role != login_role:
            user.role = login_role

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
    return user, created, reactivated


class AccountDeactivationBlockedError(ValueError):
    """Raised when an account cannot be deactivated yet: it leads a clan or is the last admin."""


@transaction.atomic
def deactivate_account(*, user: User) -> User:
    """Soft-delete ``user``'s own account and wipe their progress (#176).

    The row stays (action logs, clan totals and audits still reference it), but
    the person starts from zero if they ever sign back in: points, streak,
    cosmetics, badges, profile text, profile photo (the stored file too),
    nickname, clan memberships (including the institutional one), campaign
    participation and progress, notifications and feed posts are removed. Approved action logs stay as audit and clan
    history, but ``progress_reset_at`` keeps them out of the user's impact
    metrics. Clan totals are not reduced: like leaving a clan, deactivating
    leaves the points the person already earned for it. Pending action logs are
    rejected so a later approval cannot credit a profile that was reset.
    Outstanding refresh tokens are blacklisted, and ``is_active`` cuts off
    access tokens immediately.

    Raises:
        AccountDeactivationBlockedError: If the user leads a clan (they must
            transfer leadership or dissolve it first) or is the last active admin.
    """
    # NO KEY UPDATE keeps the user-before-profile order shared with onboarding
    # without blocking the KEY SHARE that notification foreign keys need.
    locked_users = list(
        User.objects.select_for_update(no_key=True)
        .filter(Q(role=User.Role.ADMIN, is_active=True) | Q(pk=user.pk))
        .order_by("pk")
    )
    locked = next(candidate for candidate in locked_users if candidate.pk == user.pk)
    active_admins = [
        candidate
        for candidate in locked_users
        if candidate.role == User.Role.ADMIN and candidate.is_active
    ]
    if locked.role == User.Role.ADMIN and len(active_admins) <= 1:
        raise AccountDeactivationBlockedError(
            "You are the last administrator; promote another admin first."
        )
    leads_a_clan = ClanMembership.objects.filter(
        user=locked,
        role=ClanMembership.MembershipRole.LEADER,
        status=ClanMembership.Status.ACCEPTED,
        clan__deleted_at__isnull=True,
    ).exists()
    if leads_a_clan:
        raise AccountDeactivationBlockedError(
            "You lead a clan; transfer its leadership or dissolve it first."
        )

    now = timezone.now()
    ClanMembership.objects.filter(user=locked).delete()
    CampaignParticipant.objects.filter(user=locked).delete()
    UserMissionProgress.objects.filter(user=locked).delete()
    UserBadge.objects.filter(user=locked).delete()
    Notification.all_objects.filter(user=locked).delete()
    Post.objects.filter(author=locked).delete()
    pending_logs = ActionLog.objects.filter(
        user=locked, status=ActionLog.Status.PENDING_AUDIT
    )
    pending_evidence_keys = list(
        pending_logs.exclude(evidence_object_key="").values_list(
            "evidence_object_key", flat=True
        )
    )
    # ``reviewed_by`` stays NULL: the system rejects these, not an admin.
    pending_logs.update(
        status=ActionLog.Status.REJECTED,
        reviewed_at=now,
        rejection_reason="The account was deactivated.",
    )
    for evidence_key in pending_evidence_keys:
        transaction.on_commit(
            lambda object_key=evidence_key: delete_action_evidence(object_key)
        )

    profile = ensure_profile(locked)
    previous_avatar_key = profile.avatar_object_key
    profile.bio = ""
    profile.preferences = {}
    profile.unlocked_cosmetics = []
    profile.avatar_url = ""
    profile.avatar_object_key = ""
    profile.total_points = 0
    profile.available_points = 0
    profile.current_streak = 0
    profile.last_action_date = None
    profile.visibility = UserProfile.Visibility.PUBLIC
    profile.institutional_clan = None
    profile.career = ""
    profile.onboarding_completed_at = None
    profile.nickname_changes = 0
    profile.progress_reset_at = now
    profile.save()
    if previous_avatar_key:
        user_id = locked.pk
        # Only once the deactivation commits, so a rollback keeps the photo.
        transaction.on_commit(
            lambda: delete_profile_avatar(user_id, previous_avatar_key)
        )

    locked.nickname = ""
    locked.is_active = False
    locked.deactivated_at = now
    locked.save(update_fields=["nickname", "is_active", "deactivated_at"])

    for token in OutstandingToken.objects.filter(user=locked):
        BlacklistedToken.objects.get_or_create(token=token)

    # Only after the commit, so open sockets close once the deactivation is real.
    deactivated_user_id = locked.pk
    transaction.on_commit(lambda: close_user_sockets(deactivated_user_id))

    logger.info("Account deactivated: user_id=%s", locked.pk)
    return locked


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
