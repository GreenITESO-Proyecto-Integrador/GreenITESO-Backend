"""Coverage for deactivating an account and starting over on reactivation (#176)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from green_iteso.accounts.identity.base import ExternalIdentity
from green_iteso.accounts.models import Clan, ClanMembership, User, UserProfile
from green_iteso.accounts.selectors import get_ecological_profile
from green_iteso.accounts.services import (
    AccountDeactivationBlockedError,
    deactivate_account,
    ensure_profile,
    login_with_microsoft,
    set_profile_avatar,
)
from green_iteso.actions.models import ActionCategory, ActionLog, ActionMaster
from green_iteso.campaigns.models import Campaign, CampaignParticipant
from green_iteso.campaigns.tests.helpers import campaign_data
from green_iteso.core.tests.image_helpers import (
    png_upload,
    stored_files,
    use_local_media,
)
from green_iteso.feed.models import Post, PostType
from green_iteso.gamification.models import Badge, UserBadge
from green_iteso.notifications.models import Notification, NotificationType

DEACTIVATE_URL = "/api/v1/profile/me/deactivate/"
PROFILE_URL = "/api/v1/profile/me/"


class _StubProvider:
    def __init__(self, identity: ExternalIdentity) -> None:
        self._identity = identity

    def authenticate(  # pylint: disable=unused-argument
        self, *, id_token: str, access_token: str
    ) -> ExternalIdentity:
        return self._identity


def _identity() -> ExternalIdentity:
    return ExternalIdentity(
        oid="33333333-3333-3333-3333-333333333333",
        tenant_id="tenant",
        email="ana@iteso.mx",
        given_name="Ana",
        surname="García",
        job_title="",
        department="Ingeniería de Software",
        employee_id="A01234567",
        group_ids=("g1",),
    )


def _log(
    user: User, clan: Clan, action: ActionMaster, **overrides: object
) -> ActionLog:
    values: dict[str, object] = {
        "user": user,
        "action": action,
        "institutional_clan": clan,
        "idempotency_key": f"key-{ActionLog.objects.count()}",
        "points_awarded": 10,
        "co2_kg_factor_snapshot": 2,
        "status": ActionLog.Status.APPROVED,
    }
    values.update(overrides)
    return ActionLog.objects.create(**values)


@pytest.fixture
def institutional_clan() -> Clan:
    return Clan.objects.create(name="Sistemas", type=Clan.ClanType.INSTITUTIONAL)


@pytest.fixture
def action() -> ActionMaster:
    category = ActionCategory.objects.create(code="WASTE", name="Waste")
    return ActionMaster.objects.create(
        code="DEACTIVATION_RECYCLE",
        category=category,
        name="Recycle before leaving",
        description="Progress that deactivation has to wipe",
        points=10,
        validation_type=ActionMaster.ValidationType.NONE,
    )


@pytest.fixture
def active_user(institutional_clan: Clan) -> User:
    """A user with progress in every place deactivation has to clear."""
    user = User.objects.create_user(
        email="ana@iteso.mx", password="local-only", nickname="EcoAna"
    )
    profile = ensure_profile(user)
    profile.bio = "I recycle"
    profile.preferences = {"theme": "dark"}
    profile.unlocked_cosmetics = ["gold-frame"]
    profile.avatar_url = "https://storage.googleapis.com/bucket/ana.png"
    profile.total_points = 300
    profile.available_points = 120
    profile.current_streak = 5
    profile.visibility = UserProfile.Visibility.PRIVATE
    profile.career = "Sistemas"
    profile.institutional_clan = institutional_clan
    profile.onboarding_completed_at = timezone.now()
    profile.nickname_changes = 2
    profile.save()
    ClanMembership.objects.create(user=user, clan=institutional_clan)
    private = Clan.objects.create(name="Eco", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(user=user, clan=private)
    badge = Badge.objects.create(name="Starter", description="First steps")
    user_badge = UserBadge.objects.create(user=user, badge=badge)
    Notification.objects.create(
        user=user,
        title="Hi",
        message="Welcome",
        notification_type=NotificationType.SYSTEM,
    )
    Post.objects.create(
        author=user,
        post_type=PostType.COMMUNITY_MILESTONE,
        content="Earned a badge",
        badge_user=user_badge,
    )
    campaign = Campaign.objects.create(**campaign_data(creator=user))
    CampaignParticipant.objects.create(campaign=campaign, user=user)
    return user


@pytest.mark.django_db
def test_deactivation_wipes_the_users_progress(active_user: User) -> None:
    deactivate_account(user=active_user)

    active_user.refresh_from_db()
    profile = UserProfile.objects.get(user=active_user)
    assert active_user.is_active is False
    assert active_user.deactivated_at is not None
    assert active_user.nickname == ""
    assert (profile.total_points, profile.available_points) == (0, 0)
    assert profile.current_streak == 0
    assert profile.bio == ""
    assert profile.preferences == {}
    assert profile.unlocked_cosmetics == []
    assert profile.avatar_url == ""
    assert profile.visibility == UserProfile.Visibility.PUBLIC
    assert profile.career == ""
    assert profile.institutional_clan is None
    assert profile.onboarding_completed_at is None
    assert profile.nickname_changes == 0
    assert profile.progress_reset_at is not None
    assert not ClanMembership.objects.filter(user=active_user).exists()
    assert not CampaignParticipant.objects.filter(user=active_user).exists()
    assert not UserBadge.objects.filter(user=active_user).exists()
    assert not Notification.all_objects.filter(user=active_user).exists()
    assert not Post.objects.filter(author=active_user).exists()


@pytest.mark.django_db
def test_clan_totals_and_action_history_are_kept(
    active_user: User, institutional_clan: Clan, action: ActionMaster
) -> None:
    Clan.objects.filter(pk=institutional_clan.pk).update(total_points=500)
    approved = _log(active_user, institutional_clan, action)

    deactivate_account(user=active_user)

    institutional_clan.refresh_from_db()
    assert institutional_clan.total_points == 500
    approved.refresh_from_db()
    assert approved.status == ActionLog.Status.APPROVED


@pytest.mark.django_db
def test_pending_action_logs_are_rejected_so_they_cannot_credit_a_reset_profile(
    active_user: User, institutional_clan: Clan, action: ActionMaster
) -> None:
    pending = _log(
        active_user,
        institutional_clan,
        action,
        status=ActionLog.Status.PENDING_AUDIT,
    )

    deactivate_account(user=active_user)

    pending.refresh_from_db()
    assert pending.status == ActionLog.Status.REJECTED
    assert pending.reviewed_at is not None
    assert pending.rejection_reason


@pytest.mark.django_db
def test_refresh_tokens_are_blacklisted_and_access_tokens_stop_working(
    active_user: User,
) -> None:
    refresh = RefreshToken.for_user(active_user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    assert client.get(PROFILE_URL).status_code == 200

    deactivate_account(user=active_user)

    assert BlacklistedToken.objects.filter(token__user=active_user).count() == 1
    assert client.get(PROFILE_URL).status_code == 401


@pytest.mark.django_db
def test_a_clan_leader_cannot_deactivate_until_they_hand_over_the_clan() -> None:
    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    ensure_profile(leader).save()
    clan = Clan.objects.create(name="Eco", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=leader, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    with pytest.raises(AccountDeactivationBlockedError):
        deactivate_account(user=leader)

    leader.refresh_from_db()
    assert leader.is_active is True
    assert ClanMembership.objects.filter(user=leader, clan=clan).exists()


@pytest.mark.django_db
def test_the_last_admin_cannot_deactivate_but_one_of_two_can() -> None:
    first = User.objects.create_user(
        email="a1@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    with pytest.raises(AccountDeactivationBlockedError):
        deactivate_account(user=first)

    User.objects.create_user(
        email="a2@iteso.mx", password="local-only", role=User.Role.ADMIN
    )
    deactivate_account(user=first)

    first.refresh_from_db()
    assert first.is_active is False


@pytest.mark.django_db
def test_endpoint_requires_authentication_and_explicit_confirmation(
    active_user: User,
) -> None:
    assert APIClient().post(DEACTIVATE_URL, {"confirm": True}).status_code == 401

    client = APIClient()
    client.force_authenticate(active_user)
    for payload in ({}, {"confirm": False}):
        response = client.post(DEACTIVATE_URL, payload, format="json")
        assert response.status_code == 400
        assert "confirm" in response.json()

    active_user.refresh_from_db()
    assert active_user.is_active is True


@pytest.mark.django_db
def test_endpoint_deactivates_and_reports_a_blocked_account(active_user: User) -> None:
    client = APIClient()
    client.force_authenticate(active_user)

    response = client.post(DEACTIVATE_URL, {"confirm": True}, format="json")

    assert response.status_code == 204
    active_user.refresh_from_db()
    assert active_user.is_active is False

    leader = User.objects.create_user(email="lead@iteso.mx", password="local-only")
    clan = Clan.objects.create(name="Lead", type=Clan.ClanType.PRIVATE)
    ClanMembership.objects.create(
        user=leader, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )
    client.force_authenticate(leader)
    blocked = client.post(DEACTIVATE_URL, {"confirm": True}, format="json")
    assert blocked.status_code == 400
    assert "account" in blocked.json()


@pytest.mark.django_db
def test_signing_in_again_reactivates_the_account_from_zero() -> None:
    first = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )
    profile = ensure_profile(first.user)
    profile.total_points = 300
    profile.available_points = 300
    profile.save()
    deactivate_account(user=first.user)

    again = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )

    assert again.reactivated is True
    assert again.created is False
    assert again.user.pk == first.user.pk
    again.user.refresh_from_db()
    assert again.user.is_active is True
    assert again.user.deactivated_at is None
    reset = UserProfile.objects.get(user=again.user)
    assert (reset.total_points, reset.available_points) == (0, 0)

    normal = login_with_microsoft(
        id_token="t", access_token="a", provider=_StubProvider(_identity())
    )
    assert normal.reactivated is False


@pytest.mark.django_db
def test_old_actions_stay_out_of_the_fresh_start_impact_metrics(
    active_user: User, institutional_clan: Clan, action: ActionMaster
) -> None:
    _log(active_user, institutional_clan, action)
    assert get_ecological_profile(active_user).impact_metrics.co2_kg == 2

    deactivate_account(user=active_user)

    assert get_ecological_profile(active_user).impact_metrics.co2_kg == 0

    _log(active_user, institutional_clan, action, idempotency_key="after")
    assert get_ecological_profile(active_user).impact_metrics.co2_kg == 2


@pytest.mark.django_db
def test_the_uploaded_profile_photo_is_removed_from_storage(
    active_user: User,
    settings: Any,
    tmp_path: Path,
    django_capture_on_commit_callbacks: Any,
) -> None:
    media = use_local_media(settings, tmp_path)
    set_profile_avatar(user=active_user, uploaded=png_upload())
    assert len(stored_files(media)) == 1

    with django_capture_on_commit_callbacks(execute=True):
        deactivate_account(user=active_user)

    profile = UserProfile.objects.get(user=active_user)
    assert profile.avatar_object_key == ""
    assert stored_files(media) == []
