"""Unit and integration tests for virtual exchangeable items and profile redemptions."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import concurrent.futures
import uuid

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.accounts.services import ensure_profile
from green_iteso.actions.models import ExchangeableItem
from green_iteso.actions.services import (
    InsufficientPointsError,
    redeem_exchangeable,
)


@pytest.fixture
def test_user(db: None) -> User:
    del db
    user = User.objects.create_user(
        email="testuser@iteso.mx", password="secret-local-password"
    )
    profile = ensure_profile(user)
    profile.total_points = 250
    profile.available_points = 250
    profile.unlocked_cosmetics = []
    profile.save(
        update_fields=["total_points", "available_points", "unlocked_cosmetics"]
    )
    return user


@pytest.fixture
def auth_client(test_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=test_user)
    return client


@pytest.fixture
def frame_gold(db: None) -> ExchangeableItem:
    del db
    return ExchangeableItem.objects.create(
        key="frame_gold_leaves",
        name="Marco Hojas Doradas",
        description="Marco cosmético dorado para la foto de perfil",
        category=ExchangeableItem.Category.FRAME,
        points_cost=100,
        is_active=True,
    )


@pytest.mark.django_db
def test_list_exchangeables_requires_authentication() -> None:
    client = APIClient()
    response = client.get("/api/v1/exchangeables/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_exchangeables_returns_active_ordered_by_points(
    auth_client: APIClient,
) -> None:
    ExchangeableItem.objects.create(
        key="theme_forest",
        name="Tema Bosque",
        points_cost=300,
        is_active=True,
    )
    ExchangeableItem.objects.create(
        key="badge_leaf",
        name="Insignia Hoja",
        points_cost=50,
        is_active=True,
    )
    ExchangeableItem.objects.create(
        key="disabled_item",
        name="Inactivo",
        points_cost=10,
        is_active=False,
    )

    response = auth_client.get("/api/v1/exchangeables/")
    assert response.status_code == 200
    items = response.json()["results"]
    keys = [item["key"] for item in items]
    assert "badge_leaf" in keys
    assert "theme_forest" in keys
    assert "disabled_item" not in keys


@pytest.mark.django_db
def test_retrieve_exchangeable(
    auth_client: APIClient, frame_gold: ExchangeableItem
) -> None:
    response = auth_client.get(f"/api/v1/exchangeables/{frame_gold.id}/")
    assert response.status_code == 200
    assert response.json()["key"] == "frame_gold_leaves"

    not_found = auth_client.get(f"/api/v1/exchangeables/{uuid.uuid4()}/")
    assert not_found.status_code == 404


@pytest.mark.django_db
def test_redeem_exchangeable_via_url_path_success(
    auth_client: APIClient, test_user: User, frame_gold: ExchangeableItem
) -> None:
    response = auth_client.post(f"/api/v1/exchangeables/{frame_gold.id}/redeem/")
    assert response.status_code == 201

    payload = response.json()
    assert payload["message"] == "Canjeable obtenido exitosamente."
    assert payload["unlocked_key"] == "frame_gold_leaves"
    assert payload["available_points"] == 150
    assert payload["unlocked_cosmetics"] == ["frame_gold_leaves"]

    # User profile checks: available_points decreased, total_points intact!
    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 150
    assert test_user.profile.total_points == 250  # Historical total is immutable!
    assert test_user.profile.unlocked_cosmetics == ["frame_gold_leaves"]


@pytest.mark.django_db
def test_redeem_exchangeable_via_payload_key_success(
    auth_client: APIClient, test_user: User, frame_gold: ExchangeableItem
) -> None:
    del frame_gold
    response = auth_client.post(
        "/api/v1/exchangeables/redeem/", {"item_key": "frame_gold_leaves"}
    )
    assert response.status_code == 201
    assert response.json()["available_points"] == 150
    assert response.json()["unlocked_key"] == "frame_gold_leaves"

    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 150
    assert test_user.profile.total_points == 250
    assert test_user.profile.unlocked_cosmetics == ["frame_gold_leaves"]


@pytest.mark.django_db
def test_redeem_exchangeable_insufficient_points(
    auth_client: APIClient, test_user: User, frame_gold: ExchangeableItem
) -> None:
    profile = test_user.profile
    profile.available_points = 50  # Less than frame_gold.points_cost (100)
    profile.save(update_fields=["available_points"])

    response = auth_client.post(f"/api/v1/exchangeables/{frame_gold.id}/redeem/")
    assert response.status_code == 400
    assert "insuficientes" in response.json()["error"].lower()

    profile.refresh_from_db()
    assert profile.available_points == 50
    assert profile.unlocked_cosmetics == []


@pytest.mark.django_db
def test_redeem_exchangeable_already_unlocked_prevents_double_purchase(
    auth_client: APIClient, test_user: User, frame_gold: ExchangeableItem
) -> None:
    # First purchase succeeds
    auth_client.post(f"/api/v1/exchangeables/{frame_gold.id}/redeem/")

    # Second purchase attempt fails
    response = auth_client.post(f"/api/v1/exchangeables/{frame_gold.id}/redeem/")
    assert response.status_code == 400
    assert "ya posees" in response.json()["error"].lower()

    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 150  # Deducted only once!
    assert test_user.profile.unlocked_cosmetics == ["frame_gold_leaves"]


@pytest.mark.django_db
def test_redeem_exchangeable_inactive(auth_client: APIClient, test_user: User) -> None:
    del test_user
    inactive = ExchangeableItem.objects.create(
        key="disabled_frame",
        name="Marco Inactivo",
        points_cost=10,
        is_active=False,
    )
    resp_path = auth_client.post(f"/api/v1/exchangeables/{inactive.id}/redeem/")
    assert resp_path.status_code == 404

    resp_body = auth_client.post(
        "/api/v1/exchangeables/redeem/", {"item_id": str(inactive.id)}
    )
    assert resp_body.status_code == 400
    assert "no disponible" in resp_body.json()["error"].lower()


@pytest.mark.django_db
def test_my_inventory_endpoint(
    auth_client: APIClient, test_user: User, frame_gold: ExchangeableItem
) -> None:
    del test_user  # fixture ensures profile exists; not accessed directly here
    # User redeems frame_gold
    auth_client.post(f"/api/v1/exchangeables/{frame_gold.id}/redeem/")

    response = auth_client.get("/api/v1/exchangeables/my-inventory/")
    assert response.status_code == 200
    data = response.json()
    assert data["unlocked_cosmetics"] == ["frame_gold_leaves"]


@pytest.mark.django_db(transaction=True)
def test_exchangeable_db_constraints() -> None:
    with pytest.raises(IntegrityError):
        ExchangeableItem.objects.create(key="free_item", name="Gratis", points_cost=0)


@pytest.mark.django_db(transaction=True)
def test_concurrent_redemptions_prevent_double_spending() -> None:
    item1 = ExchangeableItem.objects.create(key="item1", name="Item 1", points_cost=100)
    item2 = ExchangeableItem.objects.create(key="item2", name="Item 2", points_cost=100)

    user = User.objects.create_user(email="buyer@iteso.mx", password="pwd")
    profile = ensure_profile(user)
    profile.available_points = 150  # Only enough for ONE item!
    profile.save()

    results: list[bool] = []
    errors: list[Exception] = []

    def try_redeem(target_item: ExchangeableItem) -> None:
        try:
            connection.connect()
            redeem_exchangeable(user=user, item_key=target_item.key)
            results.append(True)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_redeem, item1)
        f2 = executor.submit(try_redeem, item2)
        concurrent.futures.wait([f1, f2])

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InsufficientPointsError)

    profile.refresh_from_db()
    assert profile.available_points == 50  # Only 100 deducted!
    assert len(profile.unlocked_cosmetics) == 1


@pytest.mark.django_db(transaction=True)
def test_actions_0006_migration_forward_and_backward() -> None:
    old_target = [
        ("actions", "0005_alter_actioncategory_table_alter_actionlog_table_and_more"),
    ]
    new_target = [
        ("actions", "0006_reward_rewardredemption"),
    ]

    executor = MigrationExecutor(connection)
    executor.migrate(old_target)

    try:
        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(new_target)
        new_apps = forward_executor.loader.project_state(new_target).apps

        item_model = new_apps.get_model("actions", "ExchangeableItem")
        item = item_model.objects.create(
            key="migrated_key", name="Migrated Item", points_cost=50
        )
        assert item_model.objects.filter(pk=item.pk).exists()

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        reverted_apps = reverse_executor.loader.project_state(old_target).apps
        with pytest.raises(LookupError):
            reverted_apps.get_model("actions", "ExchangeableItem")
    finally:
        cleanup_executor = MigrationExecutor(connection)
        cleanup_executor.migrate(cleanup_executor.loader.graph.leaf_nodes())
