# GreenITESO T9a core schema draft

**Status: T9a schema aligned with the approved ERD.** Fernando confirmed the ERD
field decisions on 2026-09-11. This document accompanies
the executable Django migrations on `feat/core-schema-review-draft`. The
approved ERD is the Notion page [9. Modelo de datos v2 (ERD con cambios
propuestos)](https://app.notion.com/p/4e7135974884836e9ac9813ca020965f), fetched
2026-09-11. It establishes the schema shape, frozen `ActionLog` clan FKs,
nullable campaign context, and `podium_snapshot`; service policy, API details,
P10/P11 behavior, and catalog values remain unresolved.

## Ownership and migration graph

The three physical Django apps are:

| App label | Team | Models in this draft |
| --- | --- | --- |
| `accounts` | E2 identity and clans | `User`, `UserProfile`, `Clan`, `ClanMembership` |
| `actions` | E1 actions and contributions | `ActionCategory`, `ActionMaster`, `ActionLog`, `ActionLogMissionContribution` |
| `campaigns` | E3 campaigns | `Campaign`, `Mission`, `CampaignParticipant`, `UserMissionProgress` |

`accounts.0001_initial` creates the custom user before any dependent model. `actions.0001_initial` creates the catalog and audit log without campaign FKs. `campaigns.0001_initial` creates campaigns and missions, then `actions.0002_actionlog_campaign_contribution` adds the campaign FK and contribution table. This split avoids a migration cycle while leaving contribution ownership with E1.

UUID primary keys follow the approved ERD. Django Admin is exposed only when local development has `DEBUG=True`; create a local administrator with `python manage.py createsuperuser`. No universal password, seeded admin, Firebase token endpoint, or simulated staging auth is included.

## Field dictionary and implementation details

### Accounts and clans

| Model.field | Type / nullability | Draft meaning and constraints |
| --- | --- | --- |
| `User.id` | UUID PK | Server generated. |
| `User.email` | email, required, unique | Institutional identity at the integration boundary; the Firebase login service must verify `@iteso.mx`. Domain validation is intentionally outside this schema draft. |
| `User.firebase_uid` | varchar 128, nullable, unique | Firebase subject, nullable for local admin/dev identities; nullable uniqueness permits many local accounts without a Firebase subject. |
| `User.role` | `STUDENT` / `STAFF` / `ADMIN` | Global application role. It is separate from `is_staff` and `is_superuser`; Django permissions control Admin access. |
| `User.is_active` | bool | Deactivation marker. Historical logs protect the account from physical deletion. |
| `UserProfile.user` | one-to-one PK/FK | Account extension. Cascade is safe for profile-only data; the account itself is protected by audit FKs. |
| `UserProfile.institutional_clan` | FK Clan, nullable | Nullable only during onboarding; the action service must reject credit before it exists. Protected to preserve history. |
| `UserProfile.career` | varchar 150, blank | Autodeclared onboarding value; Firebase does not verify it. |
| `UserProfile.onboarding_completed_at` | timestamp, nullable | Explicit onboarding marker. |
| `UserProfile.total_points` | bigint, default 0 | Denormalized total written by the points transaction; DB check is nonnegative. |
| `UserProfile.current_streak` | nonnegative integer | Derived/lazy streak projection. |
| `UserProfile.last_action_date` | local business date, nullable | Date used by the proposed Mexico City streak policy. |
| `Clan.id` | UUID PK | Server generated. |
| `Clan.name` | varchar 100, required, unique | Stable display name for the draft. Rename policy is pending product review. |
| `Clan.type` | `INSTITUTIONAL` / `PRIVATE` | Clan kind; membership eligibility and five-private-clan limit are service rules. |
| `Clan.privacy` | `PUBLIC` / `PRIVATE_INVITE` | Visibility/access hint. |
| `Clan.total_points` | bigint, default 0 | Denormalized historical contribution total; DB check is nonnegative. |
| `Clan.created_by` | FK User, nullable, PROTECT | Creator audit link. |
| `Clan.deleted_at` | timestamp, nullable | Canonical soft-delete marker. The old `is_deleted` name maps to `deleted_at IS NOT NULL`. |
| `ClanMembership.user`, `.clan` | FKs, required | Membership identity; one row per pair. Membership rows may be removed as a service operation after historical logs are protected. |
| `ClanMembership.role` | `LEADER` / `MEMBER` | Contextual role, independent of global `User.role`. |
| `ClanMembership.is_active_private` | bool | Proposed source of the active private clan. A partial unique constraint permits at most one true row per user. The service must also ensure it points to an active private clan. |
| `ClanMembership.joined_at` | timestamp | Membership start. |

### Actions and audit

| Model.field | Type / nullability | Draft meaning and constraints |
| --- | --- | --- |
| `ActionCategory.id` | UUID PK | Server generated. |
| `ActionCategory.code` | varchar 50, required, unique | Stable reference key for repeatable catalog bootstrap. |
| `ActionCategory.name`, `.description`, `.icon` | text fields | Display metadata. |
| `ActionMaster.id` | UUID PK | Server generated. |
| `ActionMaster.code` | varchar 50, required, unique | Stable action key. |
| `ActionMaster.category` | FK Category, PROTECT | Catalog ownership. |
| `ActionMaster.points` | positive integer | Proposed catalog value; must be greater than zero. |
| `ActionMaster.daily_limit` | positive integer | Proposed per-local-day limit; rolling 24-hour versus calendar-day semantics are unresolved. |
| `ActionMaster.validation_type` | `NONE` / `PHOTO` | Approved ERD name/value. QR is excluded from T9a and remains a separate scope decision. |
| `ActionMaster.co2_kg_factor`, `.water_liters_factor`, `.plastic_kg_factor` | Decimal(12,3), nonnegative | Provisional exact-decimal factors; no values are ratified by this migration. |
| `ActionMaster.is_active` | bool | Catalog availability. |
| `ActionLog.id` | UUID PK | Server generated. |
| `ActionLog.user` | FK User, PROTECT | Historical actor; deactivation is preferred over deletion. |
| `ActionLog.action` | FK ActionMaster, PROTECT | Historical catalog reference. |
| `ActionLog.institutional_clan` | FK Clan, PROTECT | Server-resolved frozen destination, required after onboarding. |
| `ActionLog.credited_private_clan` | FK Clan, PROTECT, nullable | Server-resolved frozen private destination. |
| `ActionLog.campaign` | FK Campaign, PROTECT, nullable | Approved nullable campaign context; campaign eligibility and advancement are service rules. |
| `ActionLog.idempotency_key` | varchar 128, required | Nonblank client attempt key; unique together with `user`. Payload conflict behavior is a service/API rule. |
| `ActionLog.points_awarded` | nonnegative integer | Frozen awarded points; rejection subtracts this snapshot once. |
| `ActionLog.*_factor_snapshot` | Decimal(12,3), nonnegative | Frozen impact factors used for historical reporting/reversal. |
| `ActionLog.status` | `APPROVED` / `PENDING_AUDIT` / `REJECTED` | Canonical audit state. `validation_status` is the legacy API alias. Pending photo points are provisionally credited. |
| `ActionLog.evidence_object_key` | varchar 500, blank | Private object key, never a signed URL. |
| `ActionLog.reviewed_by` | FK User, PROTECT, nullable | Auditor identity. |
| `ActionLog.reviewed_at` | timestamp, nullable | Decision timestamp. |
| `ActionLog.rejection_reason` | text, blank | Required by the audit service only when status becomes rejected. |
| `ActionLog.created_at` | timestamp | Server timestamp used by reporting and the future daily query index. |
| `ActionLogMissionContribution.id` | UUID PK | Physical surrogate key; the unique FK pair preserves the ERD logical identity. |
| `ActionLogMissionContribution.action_log`, `.mission` | FKs, PROTECT | Exact historical mission increments. The approved ERD pair is represented physically by the surrogate `id` plus a unique pair; do not infer contributions from current membership/catalog state. |
| `ActionLogMissionContribution.created_at` | timestamp | Contribution timestamp. |

### Campaigns and missions

| Model.field | Type / nullability | Draft meaning and constraints |
| --- | --- | --- |
| `Campaign.id` | UUID PK | Server generated. |
| `Campaign.title`, `.description` | text fields | Campaign content. |
| `Campaign.scope` | `GLOBAL` / `PRIVATE` | Canonical draft name/value; legacy `type=PRIVATE_CLAN` maps to `scope=PRIVATE`. |
| `Campaign.status` | `PROMOTION` / `IN_PROGRESS` / `FINISHED` | Lifecycle projection. Date-based transitions and publication/approval are service concerns. |
| `Campaign.creator` | FK User, PROTECT | Protected audit graph. Contextual leader/admin authorization is a service rule. |
| `Campaign.target_clan` | FK Clan, PROTECT, nullable | Required for `PRIVATE`, forbidden for `GLOBAL` by DB check. |
| `Campaign.start_date`, `.end_date` | aware timestamps | DB check enforces `end_date > start_date`. |
| `Campaign.podium_snapshot` | nullable JSON | Approved results snapshot column; no close/snapshot service is included. |
| `Campaign.created_at` | timestamp | Server creation time. |
| `Mission.id` | UUID PK | Server generated. |
| `Mission.campaign` | FK Campaign, PROTECT | Protected from deletion while audit/contributions reference it. |
| `Mission.action` (physical `action_master_id`) | FK ActionMaster, PROTECT | Approved ERD foreign-key name; Django keeps `action` as the Python relation and stores `action_master_id`. |
| `Mission.target_count` | positive integer | Required target; DB check is greater than zero. |
| `CampaignParticipant` | campaign/user FKs PROTECT, unique pair | Minimal enrollment graph for later progress services. |
| `UserMissionProgress` | user/mission FKs PROTECT, unique pair | Raw nonnegative count and completion projection. Cross-row consistency with mission target belongs to the service. |

## Schema decisions and remaining service work

- **P3 (approved schema shape):** retain both frozen clan FKs plus campaign context and mission contribution rows. The schema does not make columns immutable against privileged SQL; the service/admin policy must do so.
- **P8 (approved schema reservation):** retain nullable `podium_snapshot` for frozen results. Whether late rejection changes published standings remains a service/product policy.
- **P9 (approved nullable field):** retain nullable explicit `ActionLog.campaign`. One-campaign-per-record semantics and whether free actions advance campaigns remain service policy.
- **P10 (unanswered):** the draft indexes `(user, action, created_at)` and stores server timestamps. The proposed query policy is local calendar day in `America/Mexico_City`; rolling 24-hour semantics remain open.
- **P11 (unanswered):** the draft uses one partial-unique `ClanMembership.is_active_private` source per user. The proposed authority is user selection; leader membership management remains a service rule.
- **Catalog approval (unanswered):** `code`, positive points/limits, validation mode and exact-decimal factors are schema requirements. Seed values, environmental mappings and institutional approval are intentionally absent.

Ordinary row checks and foreign keys do not enforce five-member limits, clan type eligibility, contextual leader authorization, matching campaign contributions, immutable snapshots, or the points/totals invariant. Those rules belong in the transaction services and their PostgreSQL integration tests.
