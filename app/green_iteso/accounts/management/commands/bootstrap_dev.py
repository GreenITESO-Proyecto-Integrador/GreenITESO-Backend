"""Create a deterministic, local-only dataset for development demos."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands.load_catalog import (
    DEFAULT_CATALOG,
    ensure_local_database,
    load_catalog_file,
    stable_reference_id,
)
from green_iteso.actions.models import (
    ActionLog,
    ActionLogMissionContribution,
    ActionMaster,
)
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)

DEMO_NAMESPACE = uuid.UUID("07f77703-1ce2-4797-a9c3-571f6d71df7b")


def demo_id(kind: str, key: str) -> uuid.UUID:
    """Return the same UUID for a fixture entity in every local checkout."""
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{key}")


def parse_as_of(value: str | None) -> datetime:
    """Parse an optional aware ISO-8601 anchor used for reproducible campaign dates."""
    if value is None:
        return timezone.now()
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CommandError(
            "--as-of must be an ISO-8601 timestamp, for example 2030-01-15T12:00:00+00:00."
        ) from error
    if timezone.is_naive(parsed):
        raise CommandError("--as-of must include a timezone offset.")
    return parsed


def ensure_dev_environment() -> None:
    """Refuse fixture writes outside an explicitly disposable local environment."""
    environment = os.environ.get("DJANGO_ENV")
    # Django's test runner temporarily sets DEBUG=False; the authoritative
    # write guard is the explicit environment plus the deployed flag.
    if environment != "dev" or settings.DEPLOYED:
        raise CommandError(
            "bootstrap_dev is local-only: use DJANGO_ENV=dev and DJANGO_DEPLOYED=false."
        )
    ensure_local_database()


def get_or_create_demo_user(index: int, role: str) -> tuple[User, bool]:
    """Create a synthetic account without a password or Firebase identity."""
    email = f"demo-{index:02d}@example.invalid"
    identifier = demo_id("user", str(index))
    user = (
        User.objects.filter(pk=identifier).first()
        or User.objects.filter(email=email).first()
    )
    if user is not None:
        return user, False
    user = User.objects.create(
        id=identifier,
        email=email,
        role=role,
        first_name="Demo",
        last_name=f"User {index:02d}",
        firebase_uid=None,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user, True


def get_or_create_demo_clan(
    key: str,
    *,
    name: str,
    clan_type: str,
    privacy: str,
    created_by: User | None,
) -> tuple[Clan, bool]:
    """Use deterministic IDs while retaining edits made through local admin."""
    clan = (
        Clan.objects.filter(pk=demo_id("clan", key)).first()
        or Clan.objects.filter(name=name).first()
    )
    if clan is not None:
        return clan, False
    return (
        Clan.objects.create(
            id=demo_id("clan", key),
            name=name,
            description="Synthetic local-only demo clan; not an institutional catalog value.",
            type=clan_type,
            privacy=privacy,
            created_by=created_by,
        ),
        True,
    )


class Command(BaseCommand):
    """Bootstrap reference data and roughly twenty synthetic users safely."""

    help = "Create the local-only GreenITESO development/demo dataset."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--as-of",
            dest="as_of",
            help="Aware ISO-8601 anchor for campaign dates (default: current time).",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        ensure_dev_environment()
        as_of = parse_as_of(options.get("as_of"))

        draft_catalog = load_catalog_file(DEFAULT_CATALOG)
        with transaction.atomic():
            # T12 depends on T11. Keep both writes in this transaction so a
            # later fixture failure cannot leave a partial catalog bootstrap.
            call_command("load_catalog", verbosity=0)
            users: list[User] = []
            user_created = 0
            roles = [User.Role.ADMIN, User.Role.STAFF] + [User.Role.STUDENT] * 18
            for index, role in enumerate(roles, start=1):
                user, was_created = get_or_create_demo_user(index, role)
                users.append(user)
                user_created += was_created

            institutional_clans = list(
                Clan.objects.filter(
                    type=Clan.ClanType.INSTITUTIONAL,
                    pk__in=[
                        stable_reference_id("institutional-clan", item["key"])
                        for item in draft_catalog.clans
                    ],
                ).order_by("pk")
            )
            if len(institutional_clans) != 3:
                raise CommandError(
                    "The DRAFT catalog did not create all institutional clans."
                )
            private_one, _ = get_or_create_demo_clan(
                "private-01",
                name="Demo private clan 01",
                clan_type=Clan.ClanType.PRIVATE,
                privacy=Clan.Privacy.PRIVATE_INVITE,
                created_by=users[0],
            )
            private_two, _ = get_or_create_demo_clan(
                "private-02",
                name="Demo private clan 02",
                clan_type=Clan.ClanType.PRIVATE,
                privacy=Clan.Privacy.PRIVATE_INVITE,
                created_by=users[1],
            )
            private_clans = [private_one, private_two]

            profiles: dict[uuid.UUID, UserProfile] = {}
            for index, user in enumerate(users):
                clan = institutional_clans[index % len(institutional_clans)]
                profile, _ = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "institutional_clan": clan,
                        "career": f"DRAFT-CAREER-{index % len(institutional_clans) + 1}",
                        "onboarding_completed_at": as_of,
                    },
                )
                profiles[user.pk] = profile
                ClanMembership.objects.get_or_create(
                    user=user,
                    clan=clan,
                    defaults={"role": ClanMembership.MembershipRole.MEMBER},
                )
                private_clan = private_clans[index % len(private_clans)]
                ClanMembership.objects.get_or_create(
                    user=user,
                    clan=private_clan,
                    defaults={
                        "role": ClanMembership.MembershipRole.LEADER
                        if index in {0, 1}
                        else ClanMembership.MembershipRole.MEMBER,
                        "is_active_private": True,
                    },
                )

            action_codes = [item["code"] for item in draft_catalog.actions]
            actions = list(
                ActionMaster.objects.filter(code__in=action_codes).order_by("code")
            )
            if len(actions) != len(action_codes) or len(actions) < 3:
                raise CommandError(
                    "The DRAFT catalog actions are missing; run load_catalog before bootstrap_dev."
                )
            campaign, campaign_created = Campaign.objects.get_or_create(
                pk=demo_id("campaign", "active"),
                defaults={
                    "title": "Draft local sustainability campaign",
                    "description": "Synthetic campaign for local development; no product values are implied.",
                    "scope": Campaign.Scope.GLOBAL,
                    "status": Campaign.Status.IN_PROGRESS,
                    "creator": users[0],
                    "start_date": as_of - timedelta(days=30),
                    "end_date": as_of + timedelta(days=30),
                },
            )
            missions: list[Mission] = []
            for index, action in enumerate(actions[:2], start=1):
                mission, _ = Mission.objects.get_or_create(
                    pk=demo_id("mission", str(index)),
                    defaults={
                        "campaign": campaign,
                        "action": action,
                        "target_count": index + 2,
                    },
                )
                missions.append(mission)
            for user in users:
                CampaignParticipant.objects.get_or_create(campaign=campaign, user=user)

            statuses = [
                ActionLog.Status.APPROVED,
                ActionLog.Status.PENDING_AUDIT,
                ActionLog.Status.REJECTED,
            ]
            created_logs: list[ActionLog] = []
            for index in range(24):
                user = users[index % len(users)]
                action = actions[index % len(actions)]
                status = statuses[index % len(statuses)]
                log, was_created = ActionLog.objects.get_or_create(
                    pk=demo_id("action-log", str(index)),
                    defaults={
                        "user": user,
                        "action": action,
                        "institutional_clan": profiles[user.pk].institutional_clan,
                        "credited_private_clan": private_clans[
                            index % len(private_clans)
                        ],
                        "campaign": campaign if index < 12 else None,
                        "idempotency_key": f"demo-action-{index:02d}",
                        # Keep the awarded snapshot on rejected rows. The
                        # rejected status makes net totals zero; changing this
                        # value would erase the history needed for reversal.
                        "points_awarded": action.points,
                        "co2_kg_factor_snapshot": action.co2_kg_factor,
                        "water_liters_factor_snapshot": action.water_liters_factor,
                        "plastic_kg_factor_snapshot": action.plastic_kg_factor,
                        "status": status,
                        "evidence_object_key": f"demo-only/action-{index:02d}.jpg"
                        if action.validation_mode == ActionMaster.ValidationMode.PHOTO
                        else "",
                        "reviewed_by": users[1]
                        if status == ActionLog.Status.REJECTED
                        else None,
                        "reviewed_at": as_of
                        if status == ActionLog.Status.REJECTED
                        else None,
                        "rejection_reason": "Synthetic rejected example"
                        if status == ActionLog.Status.REJECTED
                        else "",
                    },
                )
                if was_created:
                    created_logs.append(log)

                if index < 12 and status != ActionLog.Status.REJECTED:
                    mission = next(
                        (
                            candidate
                            for candidate in missions
                            if candidate.action_id == action.pk
                        ),
                        None,
                    )
                    if mission is not None:
                        ActionLogMissionContribution.objects.get_or_create(
                            action_log=log, mission=mission
                        )

            for mission in missions:
                for user in users:
                    contribution_count = ActionLogMissionContribution.objects.filter(
                        mission=mission, action_log__user=user
                    ).count()
                    UserMissionProgress.objects.get_or_create(
                        user=user,
                        mission=mission,
                        defaults={
                            "current_count": contribution_count,
                            "is_completed": contribution_count >= mission.target_count,
                        },
                    )

            # Totals are denormalized projections. Recompute them on each run
            # so a local rerun repairs derived values without changing logs.
            for profile in profiles.values():
                points = (
                    ActionLog.objects.filter(user=profile.user)
                    .exclude(status=ActionLog.Status.REJECTED)
                    .values_list("points_awarded", flat=True)
                )
                profile.total_points = sum(points)
                profile.save(update_fields=["total_points"])

            for clan in [*institutional_clans, *private_clans]:
                queryset = ActionLog.objects.filter(institutional_clan=clan)
                if clan in private_clans:
                    queryset = ActionLog.objects.filter(credited_private_clan=clan)
                points = queryset.exclude(status=ActionLog.Status.REJECTED).values_list(
                    "points_awarded", flat=True
                )
                clan.total_points = sum(points)
                clan.save(update_fields=["total_points"])

        message = (
            f"Bootstrapped local demo: {len(users)} users ({user_created} created), "
            f"{len(private_clans)} private clans, {len(created_logs)} action logs, "
            f"{len(missions)} missions, campaign_created={campaign_created}."
        )
        self.stdout.write(self.style.SUCCESS(message))
        return message
