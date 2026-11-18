"""Shared identity checks for deterministic demo action logs."""

from typing import Any

from green_iteso.actions.models import ActionLog, ActionMaster

DEMO_STATUSES = (
    ActionLog.Status.APPROVED,
    ActionLog.Status.PENDING_AUDIT,
    ActionLog.Status.REJECTED,
)


def expected_demo_action_status(
    index: int, actions: list[ActionMaster]
) -> tuple[ActionMaster, str]:
    """Return the deterministic synthetic action and compatible status."""
    action = actions[index % len(actions)]
    status = DEMO_STATUSES[index % len(DEMO_STATUSES)]
    if status == ActionLog.Status.PENDING_AUDIT:
        photo_action = next(
            (
                candidate
                for candidate in actions
                if candidate.validation_type == ActionMaster.ValidationType.PHOTO
            ),
            None,
        )
        if photo_action is not None:
            action = photo_action
        else:
            status = ActionLog.Status.APPROVED
    return action, status


def demo_action_log_identity(log: ActionLog) -> tuple[Any, ...]:
    """Return immutable and review-state fields used to verify a demo log."""
    return (
        log.idempotency_key,
        log.user_id,
        log.action_id,
        log.institutional_clan_id,
        log.credited_private_clan_id,
        log.status,
        log.points_awarded,
        log.co2_kg_factor_snapshot,
        log.water_liters_factor_snapshot,
        log.plastic_kg_factor_snapshot,
        log.campaign_id,
        log.evidence_object_key,
        log.reviewed_by_id,
        log.reviewed_at is not None,
        log.rejection_reason,
    )
