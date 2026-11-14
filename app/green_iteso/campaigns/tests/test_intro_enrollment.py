"""Auto-enrollment of new users in the intro campaign."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from django.utils import timezone

from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.models import User
from green_iteso.accounts.services import LoginResult, login_with_microsoft
from green_iteso.actions.models import ActionCategory, ActionMaster
from green_iteso.campaigns.models import (
    Campaign,
    CampaignParticipant,
    Mission,
    UserMissionProgress,
)
from green_iteso.campaigns.services import enroll_in_intro_campaign

INTRO_ID = UUID("90000000-0000-4000-8000-0000000000a1")


class _StubProvider:
    def __init__(self, identity: ExternalIdentity) -> None:
        self._identity = identity

    def authenticate(  # pylint: disable=unused-argument
        self, *, id_token: str, access_token: str
    ) -> ExternalIdentity:
        return self._identity


def _identity(oid: str, email: str) -> ExternalIdentity:
    return ExternalIdentity(
        oid=oid,
        tenant_id="tenant",
        email=email,
        given_name="Ana",
        surname="García",
        job_title="",
        department="",
        employee_id="",
        group_ids=(),
    )  # type: ignore[arg-type]


def _login(oid: str, email: str) -> LoginResult:
    return login_with_microsoft(
        id_token="t",
        access_token="a",
        provider=_StubProvider(_identity(oid, email)),
    )


def _intro_campaign(*, pk: UUID = INTRO_ID, missions: int = 6) -> Campaign:
    admin = User.objects.create_user(email="admin-intro@iteso.mx", password="x")
    now = timezone.now()
    campaign = Campaign.objects.create(
        id=pk,
        title="Campaña de introducción",
        scope=Campaign.Scope.GLOBAL,
        status=Campaign.Status.IN_PROGRESS,
        creator=admin,
        approval_status=Campaign.ApprovalStatus.APPROVED,
        reviewed_by=admin,
        reviewed_at=now,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=365),
    )
    category = ActionCategory.objects.create(code="INTRO_CAT", name="Intro")
    for index in range(missions):
        action = ActionMaster.objects.create(
            code=f"INTRO_ACTION_{index}",
            category=category,
            name=f"Action {index}",
            points=1,
            validation_type=ActionMaster.ValidationType.NONE,
        )
        Mission.objects.create(campaign=campaign, action=action, target_count=1)
    return campaign


@pytest.mark.django_db
def test_new_user_is_enrolled_with_zeroed_progress_on_every_mission() -> None:
    campaign = _intro_campaign()

    result = _login("11111111-1111-1111-1111-111111111111", "new@iteso.mx")

    assert result.created is True
    assert CampaignParticipant.objects.filter(
        campaign=campaign, user=result.user
    ).exists()
    progress = UserMissionProgress.objects.filter(user=result.user)
    assert progress.count() == 6
    assert all(row.current_count == 0 and not row.is_completed for row in progress)


@pytest.mark.django_db
def test_enrollment_is_idempotent() -> None:
    _intro_campaign()
    user = User.objects.create_user(email="again@iteso.mx", password="x")

    assert enroll_in_intro_campaign(user) is True
    assert enroll_in_intro_campaign(user) is True

    assert CampaignParticipant.objects.filter(user=user).count() == 1
    assert UserMissionProgress.objects.filter(user=user).count() == 6


@pytest.mark.django_db
def test_campaign_is_found_by_title_when_the_id_differs() -> None:
    _intro_campaign(pk=UUID("90000000-0000-4000-8000-0000000000ff"), missions=2)
    user = User.objects.create_user(email="title@iteso.mx", password="x")

    assert enroll_in_intro_campaign(user) is True

    assert UserMissionProgress.objects.filter(user=user).count() == 2


@pytest.mark.django_db
def test_missing_campaign_does_not_block_login() -> None:
    result = _login("22222222-2222-2222-2222-222222222222", "nocamp@iteso.mx")

    assert result.created is True
    assert result.access
    assert not CampaignParticipant.objects.filter(user=result.user).exists()


@pytest.mark.django_db
def test_returning_user_is_not_enrolled() -> None:
    existing = User.objects.create_user(email="old@iteso.mx", password="x")
    _intro_campaign()

    result = _login("33333333-3333-3333-3333-333333333333", "old@iteso.mx")

    assert result.created is False
    assert result.user.pk == existing.pk
    assert not CampaignParticipant.objects.filter(user=existing).exists()
