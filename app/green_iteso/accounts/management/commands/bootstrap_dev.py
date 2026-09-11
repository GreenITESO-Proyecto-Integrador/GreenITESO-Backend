"""Create a deterministic, local-only dataset for development demos."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import transaction
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands.load_catalog import (
    DEFAULT_CATALOG,
    CatalogData,
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
DEMO_STATUSES = (
    ActionLog.Status.APPROVED,
    ActionLog.Status.PENDING_AUDIT,
    ActionLog.Status.REJECTED,
)


@dataclass(frozen=True)
class LogSeedContext:
    """Inputs shared by the action-log and contribution fixture builders."""

    users: list[User]
    profiles: dict[uuid.UUID, UserProfile]
    private_clans: list[Clan]
    actions: list[ActionMaster]
    campaign: Campaign
    missions: list[Mission]
    as_of: datetime


@dataclass(frozen=True)
class DemoSeedResult:
    """Small result object used to keep the command orchestration readable."""

    user_count: int
    user_created: int
    private_clan_count: int
    mission_count: int
    created_logs: int
    campaign_created: bool


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
    user = User.objects.filter(pk=identifier).first()
    if user is not None:
        return user, False
    if User.objects.filter(email=email).exists():
        raise CommandError("Demo user identity collision; existing user was preserved.")
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
    clan = Clan.objects.filter(pk=demo_id("clan", key)).first()
    if clan is not None:
        return clan, False
    if Clan.objects.filter(name=name).exists():
        raise CommandError("Demo clan identity collision; existing clan was preserved.")
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


def create_demo_users() -> tuple[list[User], int]:
    """Create the twenty synthetic accounts and report new rows."""
    roles = [User.Role.ADMIN, User.Role.STAFF] + [User.Role.STUDENT] * 18
    users: list[User] = []
    created_count = 0
    for index, role in enumerate(roles, start=1):
        user, was_created = get_or_create_demo_user(index, role)
        users.append(user)
        created_count += was_created
    return users, created_count


def get_draft_institutional_clans(catalog: CatalogData) -> list[Clan]:
    """Resolve only the institutional clans declared by the DRAFT catalog."""
    clan_ids = [
        stable_reference_id("institutional-clan", item["key"]) for item in catalog.clans
    ]
    clans = list(
        Clan.objects.filter(
            type=Clan.ClanType.INSTITUTIONAL,
            pk__in=clan_ids,
        ).order_by("pk")
    )
    if len(clans) != len(clan_ids) or len(clans) != 3:
        raise CommandError("The DRAFT catalog did not create all institutional clans.")
    return clans


def create_private_clans(users: list[User]) -> list[Clan]:
    """Create the two private clans used by the contextual demo."""
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
    return [private_one, private_two]


def create_profiles_and_memberships(
    users: list[User],
    institutional_clans: list[Clan],
    private_clans: list[Clan],
    as_of: datetime,
) -> dict[uuid.UUID, UserProfile]:
    """Create onboarding profiles and institutional/private membership rows."""
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
    return profiles


def get_draft_actions(catalog: CatalogData) -> list[ActionMaster]:
    """Resolve only action codes owned by the DRAFT catalog fixture."""
    action_codes = [item["code"] for item in catalog.actions]
    actions = list(ActionMaster.objects.filter(code__in=action_codes).order_by("code"))
    if len(actions) != len(action_codes) or len(actions) < 3:
        raise CommandError(
            "The DRAFT catalog actions are missing; run load_catalog before bootstrap_dev."
        )
    return actions


def create_campaign_and_missions(
    users: list[User], actions: list[ActionMaster], as_of: datetime
) -> tuple[Campaign, list[Mission], bool]:
    """Create one active campaign and its first two DRAFT missions."""
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
    return campaign, missions, campaign_created


def add_mission_contribution(
    index: int,
    status: str,
    log: ActionLog,
    action: ActionMaster,
    missions: list[Mission],
) -> None:
    """Attach eligible campaign logs to their matching mission."""
    if index >= 12 or status == ActionLog.Status.REJECTED:
        return
    mission = next(
        (candidate for candidate in missions if candidate.action_id == action.pk),
        None,
    )
    if mission is not None:
        ActionLogMissionContribution.objects.get_or_create(
            action_log=log, mission=mission
        )


def create_action_logs(context: LogSeedContext) -> int:
    """Create varied audit logs and exact mission contributions."""
    users = context.users
    profiles = context.profiles
    private_clans = context.private_clans
    actions = context.actions
    campaign = context.campaign
    missions = context.missions
    as_of = context.as_of
    created_count = 0
    for index in range(24):
        user = users[index % len(users)]
        action = actions[index % len(actions)]
        status = DEMO_STATUSES[index % len(DEMO_STATUSES)]
        log, was_created = ActionLog.objects.get_or_create(
            pk=demo_id("action-log", str(index)),
            defaults={
                "user": user,
                "action": action,
                "institutional_clan": profiles[user.pk].institutional_clan,
                "credited_private_clan": private_clans[index % len(private_clans)],
                "campaign": campaign if index < 12 else None,
                "idempotency_key": f"demo-action-{index:02d}",
                # Keep the awarded snapshot on rejected rows. The rejected
                # status makes net totals zero; changing this erases history.
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
                "reviewed_at": as_of if status == ActionLog.Status.REJECTED else None,
                "rejection_reason": "Synthetic rejected example"
                if status == ActionLog.Status.REJECTED
                else "",
            },
        )
        created_count += was_created
        add_mission_contribution(index, status, log, action, missions)
    return created_count


def create_mission_progress(users: list[User], missions: list[Mission]) -> None:
    """Persist progress projections from the exact contribution rows."""
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


def refresh_demo_totals(
    profiles: dict[uuid.UUID, UserProfile],
    institutional_clans: list[Clan],
    private_clans: list[Clan],
) -> None:
    """Recompute denormalized local projections without changing source logs."""
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


def seed_demo_data(catalog: CatalogData, as_of: datetime) -> DemoSeedResult:
    """Write the complete demo graph inside the caller's transaction."""
    users, user_created = create_demo_users()
    institutional_clans = get_draft_institutional_clans(catalog)
    private_clans = create_private_clans(users)
    profiles = create_profiles_and_memberships(
        users, institutional_clans, private_clans, as_of
    )
    actions = get_draft_actions(catalog)
    campaign, missions, campaign_created = create_campaign_and_missions(
        users, actions, as_of
    )
    created_logs = create_action_logs(
        LogSeedContext(
            users,
            profiles,
            private_clans,
            actions,
            campaign,
            missions,
            as_of,
        )
    )
    create_mission_progress(users, missions)
    refresh_demo_totals(profiles, institutional_clans, private_clans)
    return DemoSeedResult(
        user_count=len(users),
        user_created=user_created,
        private_clan_count=len(private_clans),
        mission_count=len(missions),
        created_logs=created_logs,
        campaign_created=campaign_created,
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
            result = seed_demo_data(draft_catalog, as_of)

        message = (
            f"Bootstrapped local demo: {result.user_count} users ({result.user_created} created), "
            f"{result.private_clan_count} private clans, {result.created_logs} action logs, "
            f"{result.mission_count} missions, campaign_created={result.campaign_created}."
        )
        return message
