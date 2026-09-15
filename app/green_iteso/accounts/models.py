"""Provisional identity and clan schema for the T9a core draft."""

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
    """Institutional account; Firebase verification is an API concern."""

    class Role(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        STAFF = "STAFF", "Staff"
        ADMIN = "ADMIN", "Administrator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField(unique=True, max_length=255)
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
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


class Clan(models.Model):
    """Institutional or private clan; deletion is represented by ``deleted_at``."""

    class ClanType(models.TextChoices):
        INSTITUTIONAL = "INSTITUTIONAL", "Institutional"
        PRIVATE = "PRIVATE", "Private"

    class Privacy(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        PRIVATE_INVITE = "PRIVATE_INVITE", "Private invite"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
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

    class Meta:
        db_table = "accounts_clan"
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
        ]
        indexes = [
            models.Index(fields=["type", "total_points"], name="clan_type_points_idx")
        ]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    """Onboarding and denormalized personal totals for the points transaction."""

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
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    total_points = models.BigIntegerField(default=0)
    current_streak = models.PositiveIntegerField(default=0)
    last_action_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "accounts_user_profile"
        constraints = [
            models.CheckConstraint(
                condition=Q(total_points__gte=0),
                name="profile_total_points_nonnegative",
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
