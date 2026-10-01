"""Unit and integration tests for the Rewards and Redemptions domain."""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.accounts.services import ensure_profile
from green_iteso.actions.models import Reward, RewardRedemption
from green_iteso.actions.services import (
    RewardOutOfStockError,
    redeem_reward,
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
    profile.save(update_fields=["total_points", "available_points"])
    return user


@pytest.fixture
def auth_client(test_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=test_user)
    return client


@pytest.fixture
def reward_mug(db: None) -> Reward:
    del db
    return Reward.objects.create(
        name="Taza Térmica ITESO",
        description="Taza de acero inoxidable con tapa hermética",
        points_cost=100,
        stock=5,
        is_active=True,
    )


@pytest.mark.django_db
def test_list_rewards_requires_authentication() -> None:
    client = APIClient()
    response = client.get("/api/v1/rewards/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_rewards_returns_active_ordered_by_points(auth_client: APIClient) -> None:
    Reward.objects.create(
        name="Reward Expensive", points_cost=300, stock=2, is_active=True
    )
    Reward.objects.create(name="Reward Cheap", points_cost=50, stock=10, is_active=True)
    Reward.objects.create(
        name="Reward Inactive", points_cost=10, stock=10, is_active=False
    )

    response = auth_client.get("/api/v1/rewards/")
    assert response.status_code == 200
    data = response.json()
    items = data["results"]
    assert len(items) == 2
    assert items[0]["name"] == "Reward Cheap"
    assert items[1]["name"] == "Reward Expensive"


@pytest.mark.django_db
def test_retrieve_reward(auth_client: APIClient, reward_mug: Reward) -> None:
    response = auth_client.get(f"/api/v1/rewards/{reward_mug.id}/")
    assert response.status_code == 200
    assert response.json()["name"] == "Taza Térmica ITESO"

    not_found = auth_client.get(f"/api/v1/rewards/{uuid.uuid4()}/")
    assert not_found.status_code == 404


@pytest.mark.django_db
def test_redeem_reward_via_url_path_success(
    auth_client: APIClient, test_user: User, reward_mug: Reward
) -> None:
    response = auth_client.post(f"/api/v1/rewards/{reward_mug.id}/redeem/")
    assert response.status_code == 201

    payload = response.json()
    assert payload["message"] == "Recompensa canjeada exitosamente."
    assert payload["available_points"] == 150
    assert payload["redemption"]["points_spent"] == 100
    assert payload["redemption"]["status"] == "COMPLETED"

    # User profile checks
    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 150
    assert test_user.profile.total_points == 250  # Historical total never decreases!

    # Reward stock checks
    reward_mug.refresh_from_db()
    assert reward_mug.stock == 4

    # DB record checks
    assert (
        RewardRedemption.objects.filter(user=test_user, reward=reward_mug).count() == 1
    )


@pytest.mark.django_db
def test_redeem_reward_via_payload_body_success(
    auth_client: APIClient, test_user: User, reward_mug: Reward
) -> None:
    response = auth_client.post(
        "/api/v1/rewards/redeem/", {"reward_id": str(reward_mug.id)}
    )
    assert response.status_code == 201
    assert response.json()["available_points"] == 150

    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 150
    assert test_user.profile.total_points == 250


@pytest.mark.django_db
def test_redeem_reward_insufficient_points(
    auth_client: APIClient, test_user: User, reward_mug: Reward
) -> None:
    profile = test_user.profile
    profile.available_points = 50  # Less than reward_mug.points_cost (100)
    profile.save(update_fields=["available_points"])

    response = auth_client.post(f"/api/v1/rewards/{reward_mug.id}/redeem/")
    assert response.status_code == 400
    assert "insuficientes" in response.json()["error"].lower()

    profile.refresh_from_db()
    assert profile.available_points == 50
    reward_mug.refresh_from_db()
    assert reward_mug.stock == 5
    assert RewardRedemption.objects.count() == 0


@pytest.mark.django_db
def test_redeem_reward_out_of_stock(
    auth_client: APIClient, test_user: User, reward_mug: Reward
) -> None:
    reward_mug.stock = 0
    reward_mug.save(update_fields=["stock"])

    response = auth_client.post(f"/api/v1/rewards/{reward_mug.id}/redeem/")
    assert response.status_code == 400
    assert "agotada" in response.json()["error"].lower()

    test_user.profile.refresh_from_db()
    assert test_user.profile.available_points == 250
    assert RewardRedemption.objects.count() == 0


@pytest.mark.django_db
def test_redeem_reward_inactive(auth_client: APIClient, test_user: User) -> None:
    inactive = Reward.objects.create(
        name="Inactive Item", points_cost=10, stock=5, is_active=False
    )
    # URL path uses queryset with is_active=True, so it returns 404
    resp_path = auth_client.post(f"/api/v1/rewards/{inactive.id}/redeem/")
    assert resp_path.status_code == 404

    # Body payload looks up the ID and catches RewardInactiveError -> 400
    resp_body = auth_client.post(
        "/api/v1/rewards/redeem/", {"reward_id": str(inactive.id)}
    )
    assert resp_body.status_code == 400
    assert "no disponible" in resp_body.json()["error"].lower()


@pytest.mark.django_db
def test_my_redemptions_history(
    auth_client: APIClient, test_user: User, reward_mug: Reward
) -> None:
    other_user = User.objects.create_user(email="other@iteso.mx", password="pwd")
    ensure_profile(other_user)
    other_user.profile.available_points = 500
    other_user.profile.save()

    # User redeems once
    auth_client.post(f"/api/v1/rewards/{reward_mug.id}/redeem/")

    # Other user redeems once
    redeem_reward(user=other_user, reward_id=reward_mug.id)

    response = auth_client.get("/api/v1/rewards/my-redemptions/")
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["reward"]["id"] == str(reward_mug.id)
    assert data["results"][0]["points_spent"] == 100


@pytest.mark.django_db(transaction=True)
def test_reward_constraints_enforced_by_database() -> None:
    with pytest.raises(IntegrityError):
        Reward.objects.create(name="Free Item", points_cost=0, stock=1)

    with pytest.raises(IntegrityError):
        Reward.objects.create(name="Negative Stock", points_cost=10, stock=-1)


@pytest.mark.django_db(transaction=True)
def test_concurrent_redemptions_prevent_overselling() -> None:
    """Ensure row-level locking (select_for_update) serializes access to stock."""
    limited_reward = Reward.objects.create(
        name="Last Item",
        points_cost=50,
        stock=1,
        is_active=True,
    )

    user1 = User.objects.create_user(email="buyer1@iteso.mx", password="pwd")
    profile1 = ensure_profile(user1)
    profile1.available_points = 100
    profile1.save()

    user2 = User.objects.create_user(email="buyer2@iteso.mx", password="pwd")
    profile2 = ensure_profile(user2)
    profile2.available_points = 100
    profile2.save()

    results: list[bool] = []
    errors: list[Exception] = []

    def try_redeem(u: User) -> None:
        try:
            connection.connect()
            redeem_reward(user=u, reward_id=limited_reward.id)
            results.append(True)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_redeem, user1)
        f2 = executor.submit(try_redeem, user2)
        concurrent.futures.wait([f1, f2])

    # Exactly one succeeded and one raised RewardOutOfStockError
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RewardOutOfStockError)

    limited_reward.refresh_from_db()
    assert limited_reward.stock == 0
    assert RewardRedemption.objects.filter(reward=limited_reward).count() == 1


@pytest.mark.django_db(transaction=True)
def test_actions_0006_migration_forward_and_backward() -> None:
    """Rehearsal required by CLAUDE.md: migrate from 0005 to 0006 and revert."""
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

        reward_model = new_apps.get_model("actions", "Reward")
        r = reward_model.objects.create(name="Migrated Reward", points_cost=50, stock=2)
        assert reward_model.objects.filter(pk=r.pk).exists()

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(old_target)
        reverted_apps = reverse_executor.loader.project_state(old_target).apps
        with pytest.raises(LookupError):
            reverted_apps.get_model("actions", "Reward")
    finally:
        cleanup_executor = MigrationExecutor(connection)
        cleanup_executor.migrate(cleanup_executor.loader.graph.leaf_nodes())
