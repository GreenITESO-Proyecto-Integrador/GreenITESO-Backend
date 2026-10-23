"""Coverage for single-use clan invite codes (#165)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.contrib.admin.sites import site as admin_site
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanInvitation, ClanMembership, User
from green_iteso.clans.invite_codes import (
    CODE_ALPHABET,
    INVITATION_RETENTION,
    MAX_ACTIVE_INVITATIONS_PER_CLAN,
    generate_code,
    hash_code,
    normalize_code,
)
from green_iteso.clans.services import (
    AlreadyClanMemberError,
    InvalidInvitationCodeError,
    InvitationLimitExceededError,
    InvitationsNotNeededError,
    PrivateClanLimitExceededError,
    create_clan_invitation,
    create_private_clan,
    dissolve_clan,
    redeem_clan_invitation,
    revoke_clan_invitation,
)
from green_iteso.clans.throttles import ClanInviteRedeemThrottle


@pytest.fixture
def invite_clan(leader: User) -> Clan:
    return create_private_clan(
        name="Secret Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _invitations_url(clan: Clan) -> str:
    return f"/api/v1/clans/{clan.pk}/invitations/"


REDEEM_URL = "/api/v1/clans/redeem-invitation/"


# --- Code helpers -----------------------------------------------------------


def test_generated_codes_use_the_unambiguous_alphabet() -> None:
    code = generate_code()

    assert len(code) == 9 and code[4] == "-"
    assert all(char in CODE_ALPHABET for char in code.replace("-", ""))
    assert not set("ILOU") & set(code)


def test_normalize_code_forgives_case_separators_and_lookalikes() -> None:
    assert normalize_code(" k7p2 qx9m ") == "K7P2QX9M"
    assert normalize_code("K7P2-QX9M") == "K7P2QX9M"
    assert normalize_code("ABCO-ILXY") == "ABC0-11XY".replace("-", "")


def test_hash_code_is_deterministic_and_never_the_code() -> None:
    assert hash_code("k7p2-qx9m") == hash_code("K7P2QX9M")
    assert "K7P2QX9M" not in hash_code("K7P2QX9M")


# --- Create -----------------------------------------------------------------


@pytest.mark.django_db
def test_leader_creates_a_code_that_is_stored_only_as_a_hash(
    leader: User, invite_clan: Clan
) -> None:
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)

    stored = ClanInvitation.objects.get(pk=invitation.pk)
    assert stored.code_hash == hash_code(code)
    assert stored.code_hint == normalize_code(code)[-4:]
    assert normalize_code(code) not in (stored.code_hash, stored.code_hint)
    expected_expiry = timezone.now() + timedelta(hours=48)
    assert abs((stored.expires_at - expected_expiry).total_seconds()) < 60


@pytest.mark.django_db
@pytest.mark.parametrize("caller", ["member", "stranger", "admin"])
def test_only_the_leader_creates_codes(caller: str, invite_clan: Clan) -> None:
    actor = User.objects.create_user(
        email=f"{caller}@iteso.mx",
        role=User.Role.ADMIN if caller == "admin" else User.Role.STUDENT,
    )
    if caller == "member":
        ClanMembership.objects.create(user=actor, clan=invite_clan)

    with pytest.raises(PermissionDenied):
        create_clan_invitation(clan=invite_clan, actor=actor)
    assert not ClanInvitation.objects.exists()


@pytest.mark.django_db
def test_create_code_rejects_an_institutional_clan(leader: User) -> None:
    clan = Clan.objects.create(name="Arquitectura", type=Clan.ClanType.INSTITUTIONAL)
    ClanMembership.objects.create(
        user=leader, clan=clan, role=ClanMembership.MembershipRole.LEADER
    )

    with pytest.raises(PermissionDenied):
        create_clan_invitation(clan=clan, actor=leader)


@pytest.mark.django_db
def test_create_code_enforces_the_active_code_cap(
    leader: User, invite_clan: Clan
) -> None:
    for _ in range(MAX_ACTIVE_INVITATIONS_PER_CLAN):
        create_clan_invitation(clan=invite_clan, actor=leader)

    with pytest.raises(InvitationLimitExceededError):
        create_clan_invitation(clan=invite_clan, actor=leader)

    revoked = ClanInvitation.objects.filter(clan=invite_clan).first()
    revoke_clan_invitation(clan=invite_clan, actor=leader, invitation_id=revoked.pk)
    create_clan_invitation(clan=invite_clan, actor=leader)


@pytest.mark.django_db
def test_api_create_returns_the_code_once_and_list_only_shows_the_hint(
    leader: User, invite_clan: Clan
) -> None:
    client = _client(leader)

    created = client.post(_invitations_url(invite_clan))
    listed = client.get(_invitations_url(invite_clan))

    assert created.status_code == 201
    code = created.json()["code"]
    assert set(created.json()) == {"id", "code", "expires_at"}
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "id": created.json()["id"],
            "code_hint": normalize_code(code)[-4:],
            "expires_at": created.json()["expires_at"],
            "created_at": listed.json()[0]["created_at"],
        }
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_api_invitations_are_leader_only(
    method: str, member: User, invite_clan: Clan
) -> None:
    ClanMembership.objects.create(user=member, clan=invite_clan)

    response = getattr(_client(member), method)(_invitations_url(invite_clan))

    assert response.status_code == 403


@pytest.mark.django_db
def test_api_invitations_on_a_dissolved_clan_return_404(
    leader: User, invite_clan: Clan
) -> None:
    dissolve_clan(clan=invite_clan, actor=leader)

    response = _client(leader).post(_invitations_url(invite_clan))

    assert response.status_code == 404


# --- List / revoke ----------------------------------------------------------


@pytest.mark.django_db
def test_list_hides_redeemed_revoked_and_expired_codes(
    leader: User, member: User, invite_clan: Clan
) -> None:
    active, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    _, used_code = create_clan_invitation(clan=invite_clan, actor=leader)
    redeem_clan_invitation(code=used_code, user=member)
    revoked, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    revoke_clan_invitation(clan=invite_clan, actor=leader, invitation_id=revoked.pk)
    expired, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    ClanInvitation.objects.filter(pk=expired.pk).update(expires_at=timezone.now())

    response = _client(leader).get(_invitations_url(invite_clan))

    assert [row["id"] for row in response.json()] == [str(active.pk)]


@pytest.mark.django_db
def test_api_leader_revokes_a_code_and_it_can_no_longer_be_redeemed(
    leader: User, member: User, invite_clan: Clan
) -> None:
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)

    response = _client(leader).delete(
        f"{_invitations_url(invite_clan)}{invitation.pk}/"
    )

    assert response.status_code == 204
    with pytest.raises(InvalidInvitationCodeError):
        redeem_clan_invitation(code=code, user=member)


@pytest.mark.django_db
def test_api_revoke_is_leader_only_and_scoped_to_the_clan(
    leader: User, member: User, invite_clan: Clan
) -> None:
    invitation, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    other_leader = User.objects.create_user(email="other-leader@iteso.mx")
    other_clan = create_private_clan(name="Other Clan", created_by=other_leader)
    ClanMembership.objects.create(user=member, clan=invite_clan)

    as_member = _client(member).delete(
        f"{_invitations_url(invite_clan)}{invitation.pk}/"
    )
    wrong_clan = _client(other_leader).delete(
        f"{_invitations_url(other_clan)}{invitation.pk}/"
    )

    assert as_member.status_code == 403
    assert wrong_clan.status_code == 404
    invitation.refresh_from_db()
    assert invitation.revoked_at is None


# --- Redeem -----------------------------------------------------------------


@pytest.mark.django_db
def test_redeem_joins_the_clan_directly_and_consumes_the_code(
    leader: User, member: User, invite_clan: Clan
) -> None:
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)

    response = _client(member).post(REDEEM_URL, {"code": code.lower()})

    assert response.status_code == 201
    assert response.json()["clan"] == str(invite_clan.pk)
    assert response.json()["status"] == ClanMembership.Status.ACCEPTED
    invitation.refresh_from_db()
    assert invitation.redeemed_by == member
    assert invitation.redeemed_at is not None
    # The clan is now visible to the new member.
    assert _client(member).get(f"/api/v1/clans/{invite_clan.pk}/").status_code == 200


@pytest.mark.django_db
def test_a_code_works_only_once(
    leader: User, member: User, successor: User, invite_clan: Clan
) -> None:
    _, code = create_clan_invitation(clan=invite_clan, actor=leader)
    redeem_clan_invitation(code=code, user=member)

    with pytest.raises(InvalidInvitationCodeError):
        redeem_clan_invitation(code=code, user=successor)


@pytest.mark.django_db
def test_unknown_and_expired_codes_get_the_same_error(
    leader: User, member: User, invite_clan: Clan
) -> None:
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)
    ClanInvitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now())
    client = _client(member)

    expired = client.post(REDEEM_URL, {"code": code})
    unknown = client.post(REDEEM_URL, {"code": "ZZZZ-ZZZZ"})
    malformed = client.post(REDEEM_URL, {"code": "abc"})

    assert expired.status_code == unknown.status_code == malformed.status_code == 400
    assert expired.json() == unknown.json() == malformed.json()
    assert "code" in expired.json()


@pytest.mark.django_db
def test_redeem_fails_once_the_clan_is_dissolved(
    leader: User, member: User, invite_clan: Clan
) -> None:
    _, code = create_clan_invitation(clan=invite_clan, actor=leader)
    dissolve_clan(clan=invite_clan, actor=leader)

    with pytest.raises(InvalidInvitationCodeError):
        redeem_clan_invitation(code=code, user=member)


@pytest.mark.django_db
def test_existing_member_gets_an_error_and_the_code_is_kept(
    leader: User, member: User, invite_clan: Clan
) -> None:
    ClanMembership.objects.create(user=member, clan=invite_clan)
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)

    with pytest.raises(AlreadyClanMemberError):
        redeem_clan_invitation(code=code, user=member)

    invitation.refresh_from_db()
    assert invitation.redeemed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "previous", [ClanMembership.Status.PENDING, ClanMembership.Status.REJECTED]
)
def test_redeem_upgrades_a_pending_or_rejected_row(
    previous: str, leader: User, member: User, invite_clan: Clan
) -> None:
    row = ClanMembership.objects.create(user=member, clan=invite_clan, status=previous)
    _, code = create_clan_invitation(clan=invite_clan, actor=leader)

    membership = redeem_clan_invitation(code=code, user=member)

    assert membership.pk == row.pk
    assert membership.status == ClanMembership.Status.ACCEPTED
    assert ClanMembership.objects.filter(user=member, clan=invite_clan).count() == 1


@pytest.mark.django_db
def test_redeem_respects_the_private_clan_cap_and_keeps_the_code(
    leader: User, member: User, invite_clan: Clan
) -> None:
    for index in range(5):
        owner = User.objects.create_user(email=f"owner{index}@iteso.mx")
        clan = create_private_clan(name=f"Clan {index}", created_by=owner)
        ClanMembership.objects.create(user=member, clan=clan)
    invitation, code = create_clan_invitation(clan=invite_clan, actor=leader)

    with pytest.raises(PrivateClanLimitExceededError):
        redeem_clan_invitation(code=code, user=member)

    invitation.refresh_from_db()
    assert invitation.redeemed_at is None


@pytest.mark.django_db
def test_redeem_is_throttled(member: User, monkeypatch: pytest.MonkeyPatch) -> None:
    # DRF reads DEFAULT_THROTTLE_RATES into a class attribute at import time,
    # as in accounts/tests/test_login_view.py.
    monkeypatch.setattr(
        ClanInviteRedeemThrottle,
        "THROTTLE_RATES",
        {"clan_invite_redeem": "2/min"},
        raising=False,
    )
    client = _client(member)

    statuses = [
        client.post(REDEEM_URL, {"code": "ZZZZ-ZZZZ"}).status_code for _ in range(3)
    ]

    assert statuses == [400, 400, 429]


@pytest.mark.django_db(transaction=True)
def test_concurrent_redemptions_of_one_code_admit_exactly_one_user(
    leader: User, member: User, successor: User
) -> None:
    clan = create_private_clan(
        name="Race Clan", created_by=leader, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    _, code = create_clan_invitation(clan=clan, actor=leader)
    outcomes: list[Exception | None] = [None, None]

    def redeem(index: int, user: User) -> None:
        try:
            redeem_clan_invitation(code=code, user=user)
        except InvalidInvitationCodeError as exc:
            outcomes[index] = exc

    threads = [
        threading.Thread(target=redeem, args=(0, member)),
        threading.Thread(target=redeem, args=(1, successor)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    joined = ClanMembership.objects.filter(
        clan=clan, role=ClanMembership.MembershipRole.MEMBER
    ).count()
    assert joined == 1
    assert sum(1 for outcome in outcomes if outcome is not None) == 1


# --- Review fixes (#204) ----------------------------------------------------


@pytest.mark.django_db
def test_api_revoke_with_a_malformed_id_returns_404(
    leader: User, invite_clan: Clan
) -> None:
    create_clan_invitation(clan=invite_clan, actor=leader)
    client = _client(leader)

    dashes = client.delete(f"{_invitations_url(invite_clan)}{'-' * 36}/")
    not_hex = client.delete(
        f"{_invitations_url(invite_clan)}{'z' * 8}-0000-0000-0000-{'0' * 12}/"
    )

    assert dashes.status_code == 404
    assert not_hex.status_code == 404
    assert list_active_count(invite_clan) == 1


def list_active_count(clan: Clan) -> int:
    return ClanInvitation.objects.filter(clan=clan, revoked_at__isnull=True).count()


@pytest.mark.django_db
def test_codes_are_only_created_for_invite_only_clans(leader: User) -> None:
    public_clan = create_private_clan(name="Open Clan", created_by=leader)

    with pytest.raises(InvitationsNotNeededError):
        create_clan_invitation(clan=public_clan, actor=leader)
    response = _client(leader).post(_invitations_url(public_clan))

    assert response.status_code == 400
    assert not ClanInvitation.objects.filter(clan=public_clan).exists()


@pytest.mark.django_db
def test_codes_issued_before_a_clan_turned_public_can_still_be_listed_and_revoked(
    leader: User, invite_clan: Clan
) -> None:
    invitation, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    Clan.objects.filter(pk=invite_clan.pk).update(privacy=Clan.Privacy.PUBLIC)
    client = _client(leader)

    listed = client.get(_invitations_url(invite_clan))
    revoked = client.delete(f"{_invitations_url(invite_clan)}{invitation.pk}/")

    assert [row["id"] for row in listed.json()] == [str(invitation.pk)]
    assert revoked.status_code == 204


@pytest.mark.django_db
def test_invitation_errors_name_invitations_not_clan_editing(
    member: User, invite_clan: Clan
) -> None:
    ClanMembership.objects.create(user=member, clan=invite_clan)

    with pytest.raises(PermissionDenied, match="manage invitation codes"):
        create_clan_invitation(clan=invite_clan, actor=member)
    with pytest.raises(PermissionDenied, match="manage invitation codes"):
        revoke_clan_invitation(
            clan=invite_clan, actor=member, invitation_id=str(uuid.uuid4())
        )


@pytest.mark.django_db
def test_redeem_treats_a_clan_deleted_mid_request_as_an_invalid_code(
    leader: User, member: User, invite_clan: Clan, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, code = create_clan_invitation(clan=invite_clan, actor=leader)
    # The clan id is read before the clan row is locked; simulate the clan
    # disappearing in between.
    monkeypatch.setattr(
        "green_iteso.clans.services._invitation_clan_id", lambda _hash: uuid.uuid4()
    )

    with pytest.raises(InvalidInvitationCodeError):
        redeem_clan_invitation(code=code, user=member)
    response = _client(member).post(REDEEM_URL, {"code": code})

    assert response.status_code == 400
    assert response.json() == {"code": ["Invalid or expired invitation code."]}


@pytest.mark.django_db
def test_every_redeem_error_is_reported_under_the_code_field(
    leader: User, member: User, invite_clan: Clan
) -> None:
    ClanMembership.objects.create(user=member, clan=invite_clan)
    _, member_code = create_clan_invitation(clan=invite_clan, actor=leader)
    capped = User.objects.create_user(email="capped@iteso.mx")
    for index in range(5):
        owner = User.objects.create_user(email=f"cap-owner{index}@iteso.mx")
        clan = create_private_clan(name=f"Cap Clan {index}", created_by=owner)
        ClanMembership.objects.create(user=capped, clan=clan)
    _, capped_code = create_clan_invitation(clan=invite_clan, actor=leader)

    already = _client(member).post(REDEEM_URL, {"code": member_code})
    over_cap = _client(capped).post(REDEEM_URL, {"code": capped_code})

    assert already.status_code == 400
    assert over_cap.status_code == 400
    assert list(already.json()) == ["code"]
    assert list(over_cap.json()) == ["code"]


@pytest.mark.django_db
def test_creating_a_code_purges_this_clans_long_expired_codes(
    leader: User, invite_clan: Clan
) -> None:
    now = timezone.now()
    old, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    recent, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    ClanInvitation.objects.filter(pk=old.pk).update(
        expires_at=now - INVITATION_RETENTION - timedelta(minutes=1)
    )
    ClanInvitation.objects.filter(pk=recent.pk).update(
        expires_at=now - INVITATION_RETENTION + timedelta(days=1)
    )
    other_owner = User.objects.create_user(email="other-owner@iteso.mx")
    other_clan = create_private_clan(
        name="Other Secret", created_by=other_owner, privacy=Clan.Privacy.PRIVATE_INVITE
    )
    other_old, _ = create_clan_invitation(clan=other_clan, actor=other_owner)
    ClanInvitation.objects.filter(pk=other_old.pk).update(
        expires_at=now - INVITATION_RETENTION - timedelta(days=1)
    )

    create_clan_invitation(clan=invite_clan, actor=leader)

    assert not ClanInvitation.objects.filter(pk=old.pk).exists()
    assert ClanInvitation.objects.filter(pk=recent.pk).exists()
    assert ClanInvitation.objects.filter(pk=other_old.pk).exists()


@pytest.mark.django_db
def test_invitation_admin_is_read_only(leader: User, invite_clan: Clan) -> None:
    invitation, _ = create_clan_invitation(clan=invite_clan, actor=leader)
    admin = User.objects.create_superuser(email="root@iteso.mx", password="local-only")
    request = RequestFactory().get("/admin/")
    request.user = admin
    model_admin = admin_site._registry[ClanInvitation]  # pylint: disable=protected-access

    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, invitation)
    assert not model_admin.has_delete_permission(request, invitation)
