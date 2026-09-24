"""Provisional identity and clan schema for the T9a core draft, extended with
profile visibility and clan soft-delete support.
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    """Keep local ``createsuperuser`` aligned with the application role."""

    use_in_migrations = True

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> User:
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> User:
        extra_fields.setdefault("role", "ADMIN")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Institutional account, identified by its Microsoft Entra object id."""

    class Role(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        STAFF = "STAFF", "Staff"
        ADMIN = "ADMIN", "Administrator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField(unique=True, max_length=255)
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    microsoft_oid = models.UUIDField(unique=True, null=True, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    # Display name shown across the app; set during onboarding, so it starts
    # blank rather than enforcing NOT NULL against pre-onboarding accounts.
    nickname = models.CharField(max_length=50, blank=True, default="")
    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=["STUDENT", "STAFF", "ADMIN"]),
                name="user_role_valid",
            ),
        ]


class ClanQuerySet(models.QuerySet):
    """Queryset helpers shared between the alive-only and unrestricted managers."""

    def alive(self) -> ClanQuerySet:
        """Return clans that have not been soft-deleted."""
        return self.filter(deleted_at__isnull=True)

    def deleted(self) -> ClanQuerySet:
        """Return only soft-deleted clans."""
        return self.filter(deleted_at__isnull=False)


class ClanManager(models.Manager.from_queryset(ClanQuerySet)):
    """Default manager: excludes soft-deleted clans from every query."""

    def get_queryset(self) -> ClanQuerySet:
        return super().get_queryset().alive()


class Clan(models.Model):
    """Institutional or private clan; deletion is represented by ``deleted_at``."""

    class ClanType(models.TextChoices):
        INSTITUTIONAL = "INSTITUTIONAL", "Institutional"
        PRIVATE = "PRIVATE", "Private"

    class Privacy(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        PRIVATE_INVITE = "PRIVATE_INVITE", "Private invite"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Not globally unique: uniqueness is scoped to alive rows by the
    # ``clan_name_unique_when_alive`` constraint below, so a soft-deleted
    # clan's name can be reused without an IntegrityError.
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    avatar_object_key = models.CharField(max_length=500, blank=True)
    type = models.CharField(max_length=16, choices=ClanType.choices)
    privacy = models.CharField(
        max_length=20, choices=Privacy.choices, default=Privacy.PUBLIC
    )
    total_points = models.BigIntegerField(default=0)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_clans",
        null=True,
        blank=True,
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    all_objects = models.Manager()
    objects = ClanManager()

    class Meta:
        db_table = "accounts_clan"
        default_manager_name = "objects"
        base_manager_name = "all_objects"
        constraints = [
            models.CheckConstraint(
                condition=Q(total_points__gte=0),
                name="clan_total_points_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(type__in=["INSTITUTIONAL", "PRIVATE"]),
                name="clan_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(privacy__in=["PUBLIC", "PRIVATE_INVITE"]),
                name="clan_privacy_valid",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="clan_name_unique_when_alive",
            ),
        ]
        indexes = [
            models.Index(fields=["type", "total_points"], name="clan_type_points_idx")
        ]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    """Onboarding and denormalized personal totals for the points transaction."""

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        PRIVATE = "PRIVATE", "Private"

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="profile",
    )
    institutional_clan = models.ForeignKey(
        "accounts.Clan",
        on_delete=models.PROTECT,
        related_name="institutional_profiles",
        null=True,
        blank=True,
    )
    career = models.CharField(max_length=150, blank=True)
    job_title = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=255, blank=True)
    employee_id = models.CharField(max_length=64, blank=True)
    microsoft_group_ids = models.JSONField(default=list, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    total_points = models.BigIntegerField(default=0)
    available_points = models.BigIntegerField(default=0)
    current_streak = models.PositiveIntegerField(default=0)
    last_action_date = models.DateField(null=True, blank=True)
    visibility = models.CharField(
        max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC
    )

    class Meta:
        db_table = "accounts_user_profile"
        constraints = [
            models.CheckConstraint(
                condition=Q(total_points__gte=0),
                name="profile_total_points_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(available_points__gte=0),
                name="profile_available_points_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(visibility__in=["PUBLIC", "PRIVATE"]),
                name="profile_visibility_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Profile: {self.user.email}"


class ClanMembership(models.Model):
    """Membership history and the single active private-clan selector."""

    class MembershipRole(models.TextChoices):
        LEADER = "LEADER", "Leader"
        MEMBER = "MEMBER", "Member"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="clan_memberships"
    )
    clan = models.ForeignKey(
        "accounts.Clan", on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=10, choices=MembershipRole.choices, default=MembershipRole.MEMBER
    )
    is_active_private = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_clan_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "clan"], name="membership_user_clan_unique"
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active_private=True),
                name="membership_one_active_private_per_user",
            ),
            models.CheckConstraint(
                condition=Q(role__in=["LEADER", "MEMBER"]),
                name="membership_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.clan.name}"
