"""Coverage for clan avatar uploads through /api/v1/clans/{id}/avatar/ (#164)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from rest_framework.test import APIClient

from green_iteso.accounts.models import Clan, ClanMembership, User
from green_iteso.clans.avatars import (
    CLAN_AVATAR_PREFIX,
    MAX_CLAN_AVATAR_BYTES,
    delete_clan_avatar,
)
from green_iteso.clans.services import create_private_clan
from green_iteso.core.tests.image_helpers import (
    PNG_BYTES,
    stored_files,
    use_fake_gcs,
    use_local_media,
    write_outsider_file,
)
from green_iteso.core.tests.image_helpers import png_upload as _png


@pytest.fixture
def avatar_media(settings: Any, tmp_path: Path) -> Path:
    return use_local_media(settings, tmp_path)


@pytest.fixture
def private_clan(leader: User) -> Clan:
    return create_private_clan(name="Green Team", created_by=leader)


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _url(clan: Clan) -> str:
    return f"/api/v1/clans/{clan.pk}/avatar/"


@pytest.mark.django_db
def test_upload_requires_authentication(private_clan: Clan) -> None:
    response = APIClient().post(_url(private_clan), {"avatar": _png()})

    assert response.status_code == 401


@pytest.mark.django_db
def test_leader_uploads_an_avatar_under_a_clan_scoped_key(
    leader: User, private_clan: Clan, avatar_media: Path
) -> None:
    response = _client(leader).post(_url(private_clan), {"avatar": _png()})

    assert response.status_code == 200
    private_clan.refresh_from_db()
    key = private_clan.avatar_object_key
    assert key.startswith(f"{CLAN_AVATAR_PREFIX}/{private_clan.pk}/")
    assert key.endswith(".png")
    assert response.json()["avatar_object_key"] == key
    assert (avatar_media / key).read_bytes() == PNG_BYTES


@pytest.mark.django_db(transaction=True)
def test_replacing_the_avatar_deletes_the_previous_file(
    leader: User, avatar_media: Path
) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = _client(leader)
    client.post(_url(clan), {"avatar": _png()})
    clan.refresh_from_db()
    first_key = clan.avatar_object_key

    client.post(_url(clan), {"avatar": _png()})

    clan.refresh_from_db()
    assert clan.avatar_object_key != first_key
    assert not (avatar_media / first_key).exists()
    assert stored_files(avatar_media) == [avatar_media / clan.avatar_object_key]


@pytest.mark.django_db(transaction=True)
def test_leader_removes_the_avatar(leader: User, avatar_media: Path) -> None:
    clan = create_private_clan(name="Green Team", created_by=leader)
    client = _client(leader)
    client.post(_url(clan), {"avatar": _png()})

    response = client.delete(_url(clan))

    assert response.status_code == 200
    clan.refresh_from_db()
    assert clan.avatar_object_key == ""
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
@pytest.mark.parametrize("caller", ["member", "admin"])
def test_only_the_leader_can_upload(
    caller: str, private_clan: Clan, avatar_media: Path
) -> None:
    actor = User.objects.create_user(
        email=f"{caller}@iteso.mx",
        role=User.Role.ADMIN if caller == "admin" else User.Role.STUDENT,
    )
    if caller == "member":
        ClanMembership.objects.create(user=actor, clan=private_clan)

    response = _client(actor).post(_url(private_clan), {"avatar": _png()})

    assert response.status_code == 403
    private_clan.refresh_from_db()
    assert private_clan.avatar_object_key == ""
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
def test_upload_rejects_a_file_that_is_not_an_image(
    leader: User, private_clan: Clan, avatar_media: Path
) -> None:
    upload = _png(payload=b"<svg onload=alert(1)>", content_type="image/png")

    response = _client(leader).post(_url(private_clan), {"avatar": upload})

    assert response.status_code == 400
    assert "avatar" in response.json()
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("avatar_media")
def test_upload_rejects_a_mismatched_declared_type(
    leader: User, private_clan: Clan
) -> None:
    upload = _png(content_type="image/jpeg")

    response = _client(leader).post(_url(private_clan), {"avatar": upload})

    assert response.status_code == 400
    assert "avatar" in response.json()


@pytest.mark.django_db
def test_upload_rejects_an_image_over_5_mb(
    leader: User, private_clan: Clan, avatar_media: Path
) -> None:
    upload = _png(payload=PNG_BYTES + b"\x00" * MAX_CLAN_AVATAR_BYTES)

    response = _client(leader).post(_url(private_clan), {"avatar": upload})

    assert response.status_code == 400
    assert "avatar" in response.json()
    assert stored_files(avatar_media) == []


@pytest.mark.django_db
def test_upload_requires_the_avatar_field(leader: User, private_clan: Clan) -> None:
    response = _client(leader).post(_url(private_clan), {}, format="multipart")

    assert response.status_code == 400
    assert "avatar" in response.json()


@pytest.mark.django_db
def test_create_and_patch_ignore_a_client_supplied_avatar_key(
    leader: User, private_clan: Clan
) -> None:
    client = _client(leader)

    patched = client.patch(
        f"/api/v1/clans/{private_clan.pk}/",
        {"avatar_object_key": "https://evil.example/x.png"},
    )
    created = _client(User.objects.create_user(email="other@iteso.mx")).post(
        "/api/v1/clans/",
        {"name": "Other Team", "avatar_object_key": "../../etc/passwd"},
    )

    assert patched.status_code == 200
    private_clan.refresh_from_db()
    assert private_clan.avatar_object_key == ""
    assert created.status_code == 201
    assert Clan.objects.get(name="Other Team").avatar_object_key == ""


def test_delete_clan_avatar_ignores_keys_outside_the_clan_prefix(
    avatar_media: Path,
) -> None:
    clan_id = UUID("00000000-0000-0000-0000-000000000001")
    outsider = write_outsider_file(avatar_media)

    delete_clan_avatar(clan_id, "action-evidence/keep.png")
    delete_clan_avatar(
        clan_id, f"{CLAN_AVATAR_PREFIX}/{clan_id}/../../action-evidence/keep.png"
    )

    assert outsider.exists()


@pytest.mark.django_db
def test_deployed_service_stores_the_avatar_in_the_configured_bucket(
    leader: User, private_clan: Clan, settings: Any, monkeypatch: Any
) -> None:
    uploads = use_fake_gcs(settings, monkeypatch)

    response = _client(leader).post(_url(private_clan), {"avatar": _png()})

    assert response.status_code == 200
    private_clan.refresh_from_db()
    assert uploads == {private_clan.avatar_object_key: (PNG_BYTES, "image/png")}
