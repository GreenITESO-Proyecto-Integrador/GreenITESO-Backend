"""Create a deterministic, local-only dataset for development demos."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.actions.management.commands.load_catalog import (
    DEFAULT_CATALOG,
    CatalogData,
    ensure_local_database,
    load_catalog_data,
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
DEMO_PRIVATE_CLAN_DESCRIPTION = (
    "Synthetic demo clan; not an institutional catalog value."
)
DEMO_INSTITUTIONAL_CLAN_DESCRIPTION = (
    "Synthetic dev-only institutional placeholder; not a canonical career."
)
LEGACY_DEMO_PRIVATE_CLAN_DESCRIPTION = (
    "Synthetic local-only demo clan; not an institutional catalog value."
)
DEMO_CAMPAIGN_DESCRIPTION = (
    "Synthetic campaign for demo use; no product values are implied."
)
LEGACY_DEMO_CAMPAIGN_DESCRIPTION = (
    "Synthetic campaign for local development; no product values are implied."
)


@dataclass(frozen=True)
class CampaignSeedContext:
    """Campaign fixture inputs shared by the demo seed stages."""

    campaign: Campaign
    missions: list[Mission]
    as_of: datetime


@dataclass(frozen=True)
class DemoClanSpec:
    """Stable identity and presentation of one synthetic clan."""

    key: str
    name: str
    clan_type: str
    privacy: str
    description: str = DEMO_PRIVATE_CLAN_DESCRIPTION


@dataclass(frozen=True)
class DemoCampaignScenario:
    """One synthetic campaign shape and its selected participants."""

    key: str
    scope: str
    status: str
    target_clan: Clan | None
    start_date: datetime
    end_date: datetime
    participants: list[User]


@dataclass(frozen=True)
class DemoSeedMetadata:
    """Catalog and mode metadata used after fixture creation."""

    institutional_clans: list[Clan]
    user_created: int
    campaigns_created: int
    campaign_count: int
    mission_count: int
    shared_dev: bool


@dataclass(frozen=True)
class LogSeedContext:
    """Inputs shared by the action-log and contribution fixture builders."""

    users: list[User]
    profiles: dict[uuid.UUID, UserProfile]
    private_clans: list[Clan]
    actions: list[ActionMaster]
    campaign: CampaignSeedContext
    metadata: DemoSeedMetadata


@dataclass(frozen=True)
class DemoActionIdentity:
    """Identity and attribution fields for one deterministic demo log."""

    user: User
    action: ActionMaster
    status: str
    institutional_clan: Clan | None
    credited_private_clan: Clan


@dataclass(frozen=True)
class SeededActionLog:
    """An idempotently created log plus its point-attribution inputs."""

    index: int
    log: ActionLog
    was_created: bool
    identity: DemoActionIdentity


@dataclass(frozen=True)
class DemoSeedResult:
    """Small result object used to keep the command orchestration readable."""

    user_count: int
    user_created: int
    private_clan_count: int
    campaign_count: int
    mission_count: int
    created_logs: int
    campaigns_created: int


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


def get_or_create_demo_user(
    index: int, role: str, *, shared_dev: bool = False
) -> tuple[User, bool]:
    """Create a synthetic account without a password or Firebase identity."""
    email = f"demo-{index:02d}@example.invalid"
    identifier = demo_id("user", str(index))
    try:
        with transaction.atomic():
            user, created = User.objects.get_or_create(
                pk=identifier,
                defaults={
                    "email": email,
                    "role": role,
                    "first_name": "Demo",
                    "last_name": f"User {index:02d}",
                    "firebase_uid": None,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
    except IntegrityError as error:
        raise CommandError(
            "Demo user identity collision; existing user was preserved."
        ) from error
    if user.email != email:
        raise CommandError("Demo user identity collision; existing user was preserved.")
    if shared_dev and (user.firebase_uid is not None or user.microsoft_oid is not None):
        raise CommandError(
            "Shared-dev demo identity is linked to an identity provider; refusing synthetic use."
        )
    return user, created


def get_or_create_demo_clan(
    spec: DemoClanSpec, *, created_by: User | None
) -> tuple[Clan, bool]:
    """Use deterministic IDs while retaining edits made through local admin."""
    identifier = demo_id("clan", spec.key)
    if Clan.all_objects.filter(name=spec.name).exclude(pk=identifier).exists():
        raise CommandError("Demo clan identity collision; existing clan was preserved.")
    try:
        clan, created = Clan.all_objects.get_or_create(
            pk=identifier,
            defaults={
                "name": spec.name,
                "description": spec.description,
                "type": spec.clan_type,
                "privacy": spec.privacy,
                "created_by": created_by,
            },
        )
    except IntegrityError as error:
        raise CommandError(
            "Demo clan identity collision; existing clan was preserved."
        ) from error
    allowed_descriptions = (
        (DEMO_PRIVATE_CLAN_DESCRIPTION, LEGACY_DEMO_PRIVATE_CLAN_DESCRIPTION)
        if spec.description == DEMO_PRIVATE_CLAN_DESCRIPTION
        else (spec.description,)
    )
    if (
        clan.deleted_at is not None
        or clan.type != spec.clan_type
        or not clan.description.startswith(allowed_descriptions)
    ):
        raise CommandError("Demo clan identity collision; existing clan was preserved.")
    return clan, created


def create_demo_users(*, shared_dev: bool = False) -> tuple[list[User], int]:
    """Create synthetic accounts; shared-dev personas are students only."""
    roles = (
        [User.Role.STUDENT] * 20
        if shared_dev
        else [User.Role.ADMIN, User.Role.STAFF] + [User.Role.STUDENT] * 18
    )
    users: list[User] = []
    created_count = 0
    for index, role in enumerate(roles, start=1):
        user, was_created = get_or_create_demo_user(index, role, shared_dev=shared_dev)
        if shared_dev and user.role != User.Role.STUDENT:
            raise CommandError(
                "Shared-dev demo identity collision; expected a student account."
            )
        users.append(user)
        created_count += was_created
    return users, created_count


def get_catalog_institutional_clans(catalog: CatalogData) -> list[Clan]:
    """Resolve only institutional clans declared by the supplied catalog."""
    clan_ids = [
        stable_reference_id("institutional-clan", item["key"]) for item in catalog.clans
    ]
    clans = list(
        Clan.objects.filter(
            type=Clan.ClanType.INSTITUTIONAL,
            pk__in=clan_ids,
        ).order_by("pk")
    )
    if not clan_ids or len(clans) != len(clan_ids):
        raise CommandError("The catalog did not create all institutional clans.")
    return clans


def create_dev_institutional_clans() -> tuple[list[Clan], dict[uuid.UUID, str]]:
    """Provide explicit dev-only career placeholders until E2 maps real careers."""
    clans: list[Clan] = []
    careers: dict[uuid.UUID, str] = {}
    for index in range(1, 4):
        clan, _ = get_or_create_demo_clan(
            DemoClanSpec(
                key=f"institutional-{index:02d}",
                name=f"Demo institutional clan {index:02d}",
                clan_type=Clan.ClanType.INSTITUTIONAL,
                privacy=Clan.Privacy.PUBLIC,
                description=DEMO_INSTITUTIONAL_CLAN_DESCRIPTION,
            ),
            created_by=None,
        )
        clans.append(clan)
        careers[clan.pk] = f"DEMO-CAREER-{index:02d}"
    return clans, careers


def create_private_clans(users: list[User]) -> list[Clan]:
    """Create the two private clans used by the contextual demo."""
    private_one, _ = get_or_create_demo_clan(
        DemoClanSpec(
            key="private-01",
            name="Demo private clan 01",
            clan_type=Clan.ClanType.PRIVATE,
            privacy=Clan.Privacy.PRIVATE_INVITE,
        ),
        created_by=users[0],
    )
    private_two, _ = get_or_create_demo_clan(
        DemoClanSpec(
            key="private-02",
            name="Demo private clan 02",
            clan_type=Clan.ClanType.PRIVATE,
            privacy=Clan.Privacy.PRIVATE_INVITE,
        ),
        created_by=users[1],
    )
    return [private_one, private_two]


def create_profiles_and_memberships(
    users: list[User],
    institutional_clans: list[Clan],
    private_clans: list[Clan],
    as_of: datetime,
    careers: dict[uuid.UUID, str] | None = None,
) -> dict[uuid.UUID, UserProfile]:
    """Create onboarding profiles and institutional/private membership rows."""
    profiles: dict[uuid.UUID, UserProfile] = {}
    for index, user in enumerate(users):
        clan = institutional_clans[index % len(institutional_clans)]
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "institutional_clan": clan,
                "career": (
                    careers[clan.pk]
                    if careers
                    else f"DRAFT-CAREER-{index % len(institutional_clans) + 1}"
                ),
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


def get_catalog_actions(catalog: CatalogData) -> list[ActionMaster]:
    """Use the fixture's stable active set, retaining all-inactive draft support."""
    action_codes = [item["code"] for item in catalog.actions]
    actions = list(ActionMaster.objects.filter(code__in=action_codes).order_by("code"))
    if len(actions) != len(action_codes) or len(actions) < 3:
        raise CommandError(
            "Catalog actions are missing; load the catalog before seeding demo data."
        )
    active_codes = {item["code"] for item in catalog.actions if item["is_active"]}
    return [action for action in actions if action.code in active_codes] or actions


def create_campaign_and_missions(
    users: list[User], actions: list[ActionMaster], as_of: datetime
) -> tuple[Campaign, list[Mission], bool]:
    """Create one active campaign and its first two DRAFT missions."""
    campaign, campaign_created = Campaign.objects.get_or_create(
        pk=demo_id("campaign", "active"),
        defaults={
            "title": "Draft local sustainability campaign",
            "description": DEMO_CAMPAIGN_DESCRIPTION,
            "scope": Campaign.Scope.GLOBAL,
            "status": Campaign.Status.IN_PROGRESS,
            "creator": users[0],
            "start_date": as_of - timedelta(days=30),
            "end_date": as_of + timedelta(days=30),
        },
    )
    if not campaign_created and (
        campaign.creator_id != users[0].pk
        or not campaign.description.startswith(
            (DEMO_CAMPAIGN_DESCRIPTION, LEGACY_DEMO_CAMPAIGN_DESCRIPTION)
        )
    ):
        raise CommandError(
            "Demo campaign identity collision; existing campaign was preserved."
        )
    missions: list[Mission] = []
    for index, action in enumerate(actions[:2], start=1):
        mission, was_created = Mission.objects.get_or_create(
            pk=demo_id("mission", str(index)),
            defaults={
                "campaign": campaign,
                "action": action,
                "target_count": index + 2,
            },
        )
        if not was_created and (
            mission.campaign_id != campaign.pk or mission.action_id != action.pk
        ):
            raise CommandError(
                "Demo mission identity collision; existing mission was preserved. "
                "See docs/semillas-locales.md before repairing legacy demo data."
            )
        missions.append(mission)
    for user in users:
        CampaignParticipant.objects.get_or_create(campaign=campaign, user=user)
    return campaign, missions, campaign_created


def create_additional_campaigns(
    users: list[User],
    actions: list[ActionMaster],
    private_clans: list[Clan],
    as_of: datetime,
) -> tuple[int, int]:
    """Cover upcoming, finished, and private campaign views with synthetic rows."""
    scenarios = (
        DemoCampaignScenario(
            key="upcoming",
            scope=Campaign.Scope.GLOBAL,
            status=Campaign.Status.PROMOTION,
            target_clan=None,
            start_date=as_of + timedelta(days=7),
            end_date=as_of + timedelta(days=14),
            participants=[],
        ),
        DemoCampaignScenario(
            key="finished",
            scope=Campaign.Scope.GLOBAL,
            status=Campaign.Status.FINISHED,
            target_clan=None,
            start_date=as_of - timedelta(days=30),
            end_date=as_of - timedelta(days=1),
            participants=users[:8],
        ),
        DemoCampaignScenario(
            key="private",
            scope=Campaign.Scope.PRIVATE,
            status=Campaign.Status.IN_PROGRESS,
            target_clan=private_clans[0],
            start_date=as_of - timedelta(days=7),
            end_date=as_of + timedelta(days=7),
            participants=users[:16:2],
        ),
    )
    created_count = 0
    for index, scenario in enumerate(scenarios):
        campaign, was_created = Campaign.objects.get_or_create(
            pk=demo_id("campaign", scenario.key),
            defaults={
                "title": f"Demo {scenario.key} campaign",
                "description": DEMO_CAMPAIGN_DESCRIPTION,
                "scope": scenario.scope,
                "status": scenario.status,
                "creator": users[0],
                "target_clan": scenario.target_clan,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
            },
        )
        if not was_created and (
            campaign.creator_id != users[0].pk
            or campaign.scope != scenario.scope
            or campaign.target_clan_id != getattr(scenario.target_clan, "pk", None)
            or not campaign.description.startswith(DEMO_CAMPAIGN_DESCRIPTION)
        ):
            raise CommandError(
                "Demo campaign identity collision; existing campaign was preserved."
            )
        created_count += was_created
        mission, mission_created = Mission.objects.get_or_create(
            pk=demo_id("mission", scenario.key),
            defaults={
                "campaign": campaign,
                "action": actions[index % len(actions)],
                "target_count": index + 2,
            },
        )
        if not mission_created and (
            mission.campaign_id != campaign.pk
            or mission.action_id != actions[index % len(actions)].pk
        ):
            raise CommandError(
                "Demo mission identity collision; existing mission was preserved. "
                "See docs/semillas-locales.md before repairing legacy demo data."
            )
        for user in scenario.participants:
            CampaignParticipant.objects.get_or_create(campaign=campaign, user=user)
        create_mission_progress(scenario.participants, [mission])
    return created_count, len(scenarios)


def add_mission_contribution(
    index: int,
    status: str,
    log: ActionLog,
    action: ActionMaster,
    missions: list[Mission],
) -> None:
    """Attach eligible campaign logs to their matching mission."""
    if index >= 12 or status != ActionLog.Status.APPROVED:
        return
    mission = next(
        (candidate for candidate in missions if candidate.action_id == action.pk),
        None,
    )
    if mission is not None:
        ActionLogMissionContribution.objects.get_or_create(
            action_log=log, mission=mission
        )


def create_demo_action_log(
    index: int, context: LogSeedContext, demo_reviewer: User | None
) -> SeededActionLog:
    """Create or validate one stable synthetic action log."""
    user = context.users[index % len(context.users)]
    action = context.actions[index % len(context.actions)]
    status = DEMO_STATUSES[index % len(DEMO_STATUSES)]
    if status == ActionLog.Status.PENDING_AUDIT:
        photo_action = next(
            (
                candidate
                for candidate in context.actions
                if candidate.validation_type == ActionMaster.ValidationType.PHOTO
            ),
            None,
        )
        if photo_action is not None:
            action = photo_action
        else:
            status = ActionLog.Status.APPROVED
    idempotency_key = f"demo-action-{index:02d}"
    institutional_clan = context.profiles[user.pk].institutional_clan
    credited_private_clan = context.private_clans[index % len(context.private_clans)]
    log, was_created = ActionLog.objects.get_or_create(
        pk=demo_id("action-log", str(index)),
        defaults={
            "user": user,
            "action": action,
            "institutional_clan": institutional_clan,
            "credited_private_clan": credited_private_clan,
            "campaign": context.campaign.campaign if index < 12 else None,
            "idempotency_key": idempotency_key,
            # Keep the awarded snapshot on rejected rows. The rejected
            # status makes net totals zero; changing this erases history.
            "points_awarded": action.points,
            "co2_kg_factor_snapshot": action.co2_kg_factor,
            "water_liters_factor_snapshot": action.water_liters_factor,
            "plastic_kg_factor_snapshot": action.plastic_kg_factor,
            "status": status,
            "evidence_object_key": f"demo-only/action-{index:02d}.jpg"
            if action.validation_type == ActionMaster.ValidationType.PHOTO
            else "",
            "reviewed_by": demo_reviewer
            if status == ActionLog.Status.REJECTED
            else None,
            # Shared Neon dev deliberately has no synthetic privileged users;
            # this row models a system-simulated rejection, not a staff audit.
            "reviewed_at": context.campaign.as_of
            if status == ActionLog.Status.REJECTED
            else None,
            "rejection_reason": (
                "Synthetic rejected example"
                if demo_reviewer is not None
                else "Synthetic system-rejected example"
            )
            if status == ActionLog.Status.REJECTED
            else "",
        },
    )
    expected_identity = (
        idempotency_key,
        user.pk,
        action.pk,
        getattr(institutional_clan, "pk", None),
        credited_private_clan.pk,
    )
    existing_identity = (
        log.idempotency_key,
        log.user_id,
        log.action_id,
        log.institutional_clan_id,
        log.credited_private_clan_id,
    )
    if not was_created and existing_identity != expected_identity:
        raise CommandError(
            "Demo action-log identity collision; existing row was preserved. "
            "See docs/semillas-locales.md before repairing legacy demo data."
        )
    return SeededActionLog(
        index,
        log,
        was_created,
        DemoActionIdentity(
            user,
            action,
            status,
            institutional_clan,
            credited_private_clan,
        ),
    )


def accumulate_approved_points(
    seeded_log: SeededActionLog,
    user_points: dict[uuid.UUID, int],
    clan_points: dict[uuid.UUID, int],
) -> None:
    """Collect only newly created approved-log points for projection updates."""
    if (
        not seeded_log.was_created
        or seeded_log.identity.status != ActionLog.Status.APPROVED
    ):
        return
    user_points[seeded_log.identity.user.pk] = (
        user_points.get(seeded_log.identity.user.pk, 0)
        + seeded_log.identity.action.points
    )
    for clan in (
        seeded_log.identity.institutional_clan,
        seeded_log.identity.credited_private_clan,
    ):
        if clan is not None:
            clan_points[clan.pk] = (
                clan_points.get(clan.pk, 0) + seeded_log.identity.action.points
            )


def create_action_logs(
    context: LogSeedContext,
) -> tuple[int, dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Create varied audit logs and exact mission contributions."""
    created_count = 0
    user_points: dict[uuid.UUID, int] = {}
    clan_points: dict[uuid.UUID, int] = {}
    demo_reviewer = next(
        (
            candidate
            for candidate in context.users
            if candidate.role in {User.Role.ADMIN, User.Role.STAFF}
        ),
        None,
    )
    for index in range(24):
        seeded_log = create_demo_action_log(index, context, demo_reviewer)
        created_count += seeded_log.was_created
        if seeded_log.was_created:
            add_mission_contribution(
                seeded_log.index,
                seeded_log.identity.status,
                seeded_log.log,
                seeded_log.identity.action,
                context.campaign.missions,
            )
            accumulate_approved_points(seeded_log, user_points, clan_points)
    return created_count, user_points, clan_points


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


@transaction.atomic
def refresh_profile_totals(
    profiles: dict[uuid.UUID, UserProfile],
    approved_points: dict[uuid.UUID, int],
    shared_dev: bool,
) -> None:
    """Refresh user projections without overwriting shared-dev API writes."""
    for profile in profiles.values():
        available_points = approved_points.get(profile.user_id, 0)
        if shared_dev:
            UserProfile.objects.filter(pk=profile.pk).update(
                total_points=F("total_points") + available_points,
                available_points=F("available_points") + available_points,
            )
        else:
            # API credits update this row too; lock before reading the logs so
            # an absolute local recomputation cannot overwrite their increment.
            locked_profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
            points = ActionLog.objects.filter(
                user_id=locked_profile.user_id, status=ActionLog.Status.APPROVED
            ).values_list("points_awarded", flat=True)
            total_points = sum(points)
            # Preserve the spent balance when local log status edits change
            # earned points; never restore redeemed points during a rerun.
            available_points = (
                locked_profile.available_points + total_points - locked_profile.total_points
            )
            if (
                not 0 <= locked_profile.available_points <= locked_profile.total_points
                or available_points < 0
            ):
                raise CommandError(
                    "Local demo balance cannot be reconciled without restoring spent points. "
                    "See docs/semillas-locales.md for an isolated local reset."
                )
            locked_profile.total_points = total_points
            locked_profile.available_points = available_points
            locked_profile.save(update_fields=["total_points", "available_points"])


@transaction.atomic
def refresh_clan_totals(
    institutional_clans: list[Clan],
    private_clans: list[Clan],
    approved_points: dict[uuid.UUID, int],
    shared_dev: bool,
) -> None:
    """Refresh clan totals atomically in shared dev and from logs locally."""
    if shared_dev:
        # Match the API's institutional-then-private row-lock order, including
        # clans retained on edited profiles outside the fixture lists.
        for clan in Clan.all_objects.filter(pk__in=approved_points).order_by(
            "type", "pk"
        ):
            points = approved_points[clan.pk]
            if points:
                Clan.all_objects.filter(pk=clan.pk).update(
                    total_points=F("total_points") + points
                )
        return
    clans = (
        *sorted(institutional_clans, key=lambda row: row.pk),
        *sorted(private_clans, key=lambda row: row.pk),
    )
    for clan in clans:
        Clan.all_objects.select_for_update().get(pk=clan.pk)
        queryset = ActionLog.objects.filter(
            institutional_clan=clan, status=ActionLog.Status.APPROVED
        )
        if clan in private_clans:
            queryset = ActionLog.objects.filter(
                credited_private_clan=clan, status=ActionLog.Status.APPROVED
            )
        points = queryset.values_list("points_awarded", flat=True)
        Clan.all_objects.filter(pk=clan.pk).update(total_points=sum(points))


def refresh_demo_totals(
    context: LogSeedContext,
    approved_user_points: dict[uuid.UUID, int],
    approved_clan_points: dict[uuid.UUID, int],
) -> None:
    """Refresh point projections without overwriting concurrent shared-dev writes."""
    refresh_profile_totals(
        context.profiles, approved_user_points, context.metadata.shared_dev
    )
    institutional_clans = context.metadata.institutional_clans
    private_clans = context.private_clans
    if not context.metadata.shared_dev:
        # Profiles can retain an admin-selected clan outside the fixture. Use
        # the stored snapshots on every rerun, including previously seeded logs.
        clan_ids = set()
        for institutional_id, private_id in ActionLog.objects.filter(
            pk__in=[demo_id("action-log", str(index)) for index in range(24)]
        ).values_list("institutional_clan_id", "credited_private_clan_id"):
            clan_ids.update(
                identifier
                for identifier in (institutional_id, private_id)
                if identifier
            )
        clans = list(Clan.all_objects.filter(pk__in=clan_ids))
        institutional_clans = list(
            {
                clan.pk: clan
                for clan in (*institutional_clans, *clans)
                if clan.type == Clan.ClanType.INSTITUTIONAL
            }.values()
        )
        private_clans = list(
            {
                clan.pk: clan
                for clan in (*private_clans, *clans)
                if clan.type == Clan.ClanType.PRIVATE
            }.values()
        )
    refresh_clan_totals(
        institutional_clans,
        private_clans,
        approved_clan_points,
        context.metadata.shared_dev,
    )


def create_log_seed_context(
    catalog: CatalogData, as_of: datetime, shared_dev: bool
) -> LogSeedContext:
    """Resolve the catalog and deterministic fixtures needed by the seed."""
    users, user_created = create_demo_users(shared_dev=shared_dev)
    if shared_dev and not catalog.clans:
        institutional_clans, careers = create_dev_institutional_clans()
    else:
        institutional_clans = get_catalog_institutional_clans(catalog)
        careers = (
            {
                stable_reference_id("institutional-clan", item["key"]): item["career"]
                for item in catalog.clans
            }
            if shared_dev
            else None
        )
    private_clans = create_private_clans(users)
    profiles = create_profiles_and_memberships(
        users, institutional_clans, private_clans, as_of, careers=careers
    )
    actions = get_catalog_actions(catalog)
    campaign, missions, campaign_created = create_campaign_and_missions(
        users, actions, as_of
    )
    additional_created, additional_scenarios = create_additional_campaigns(
        users, actions, private_clans, as_of
    )
    return LogSeedContext(
        users=users,
        profiles=profiles,
        private_clans=private_clans,
        actions=actions,
        campaign=CampaignSeedContext(campaign, missions, as_of),
        metadata=DemoSeedMetadata(
            institutional_clans=institutional_clans,
            user_created=user_created,
            campaigns_created=int(campaign_created) + additional_created,
            campaign_count=1 + additional_scenarios,
            mission_count=len(missions) + additional_scenarios,
            shared_dev=shared_dev,
        ),
    )


def seed_demo_data(
    catalog: CatalogData, as_of: datetime, *, shared_dev: bool = False
) -> DemoSeedResult:
    """Write the complete demo graph inside the caller's transaction."""
    context = create_log_seed_context(catalog, as_of, shared_dev)
    created_logs, approved_user_points, approved_clan_points = create_action_logs(
        context
    )
    create_mission_progress(context.users, context.campaign.missions)
    refresh_demo_totals(context, approved_user_points, approved_clan_points)
    return DemoSeedResult(
        user_count=len(context.users),
        user_created=context.metadata.user_created,
        private_clan_count=len(context.private_clans),
        campaign_count=context.metadata.campaign_count,
        mission_count=context.metadata.mission_count,
        created_logs=created_logs,
        campaigns_created=context.metadata.campaigns_created,
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
            load_catalog_data(draft_catalog)
            result = seed_demo_data(draft_catalog, as_of)

        message = (
            f"Bootstrapped local demo: {result.user_count} users ({result.user_created} created), "
            f"{result.private_clan_count} private clans, {result.created_logs} action logs, "
            f"{result.campaign_count} campaigns ({result.campaigns_created} created), "
            f"{result.mission_count} missions."
        )
        return message
