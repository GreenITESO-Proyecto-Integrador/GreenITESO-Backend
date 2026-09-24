"""API and model checks for the user notification feed."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from green_iteso.accounts.models import User
from green_iteso.notifications.models import Notification


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email="owner@iteso.mx", password="local-password")


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user(email="other@iteso.mx", password="local-password")


@pytest.fixture
def client(user: User) -> APIClient:
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
def test_notification_type_check_constraint_is_enforced(user: User) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Notification.objects.create(
                user=user,
                title="Bad",
                message="Bad type",
                notification_type="NOT_A_REAL_TYPE",
            )


@pytest.mark.django_db
def test_notification_str_and_defaults(user: User) -> None:
    notification = Notification.objects.create(
        user=user,
        title="Badge earned",
        message="You earned the Recycler badge",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )
    assert notification.is_read is False
    assert str(notification) == f"{user.id}: BADGE_EARNED"


@pytest.mark.django_db
def test_list_endpoint_requires_authentication() -> None:
    response = APIClient().get(reverse("notifications:list"))
    assert response.status_code == 401 or response.status_code == 403


@pytest.mark.django_db
def test_list_returns_only_owned_notifications_newest_first(
    client: APIClient, user: User, other_user: User
) -> None:
    Notification.objects.create(
        user=other_user,
        title="Not yours",
        message="Should not appear",
        notification_type=Notification.NotificationType.MISSION_COMPLETED,
    )
    older = Notification.objects.create(
        user=user,
        title="Older",
        message="First",
        notification_type=Notification.NotificationType.CAMPAIGN_INVITE,
    )
    newer = Notification.objects.create(
        user=user,
        title="Newer",
        message="Second",
        notification_type=Notification.NotificationType.AUDIT_REJECT,
    )

    response = client.get(reverse("notifications:list"))

    assert response.status_code == 200
    results = response.data["results"]
    assert [item["id"] for item in results] == [str(newer.id), str(older.id)]
    assert response.data["unread_count"] == 2
    assert results[0]["notification_type_display"] == "Photo audit rejected"
    assert "ago" in results[0]["time_since_created"]


@pytest.mark.django_db
def test_list_filters_by_is_read(client: APIClient, user: User) -> None:
    read_notification = Notification.objects.create(
        user=user,
        title="Read",
        message="Already read",
        notification_type=Notification.NotificationType.BADGE_EARNED,
        is_read=True,
    )
    unread_notification = Notification.objects.create(
        user=user,
        title="Unread",
        message="Still unread",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )

    unread_response = client.get(reverse("notifications:list"), {"is_read": "false"})
    read_response = client.get(reverse("notifications:list"), {"is_read": "true"})

    assert [item["id"] for item in unread_response.data["results"]] == [
        str(unread_notification.id)
    ]
    assert [item["id"] for item in read_response.data["results"]] == [
        str(read_notification.id)
    ]


@pytest.mark.django_db
def test_unread_count_endpoint(client: APIClient, user: User) -> None:
    Notification.objects.create(
        user=user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )
    Notification.objects.create(
        user=user,
        title="B",
        message="B",
        notification_type=Notification.NotificationType.BADGE_EARNED,
        is_read=True,
    )

    response = client.get(reverse("notifications:unread-count"))

    assert response.status_code == 200
    assert response.data == {"unread_count": 1}


@pytest.mark.django_db
def test_patch_marks_notification_as_read(client: APIClient, user: User) -> None:
    notification = Notification.objects.create(
        user=user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.MISSION_COMPLETED,
    )

    response = client.patch(
        reverse("notifications:detail", kwargs={"pk": notification.id}),
        {"is_read": True},
        format="json",
    )

    assert response.status_code == 200
    notification.refresh_from_db()
    assert notification.is_read is True
    assert response.data["is_read"] is True


@pytest.mark.django_db
def test_patch_cannot_modify_another_users_notification(
    client: APIClient, other_user: User
) -> None:
    notification = Notification.objects.create(
        user=other_user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.MISSION_COMPLETED,
    )

    response = client.patch(
        reverse("notifications:detail", kwargs={"pk": notification.id}),
        {"is_read": True},
        format="json",
    )

    assert response.status_code == 404
    notification.refresh_from_db()
    assert notification.is_read is False


@pytest.mark.django_db
def test_mark_all_read_updates_only_current_user(
    client: APIClient, user: User, other_user: User
) -> None:
    Notification.objects.create(
        user=user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )
    Notification.objects.create(
        user=user,
        title="B",
        message="B",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )
    other_notification = Notification.objects.create(
        user=other_user,
        title="C",
        message="C",
        notification_type=Notification.NotificationType.BADGE_EARNED,
    )

    response = client.patch(reverse("notifications:mark-all-read"), {}, format="json")

    assert response.status_code == 200
    assert response.data == {"marked_as_read": 2}
    assert Notification.objects.filter(user=user, is_read=False).count() == 0
    other_notification.refresh_from_db()
    assert other_notification.is_read is False


@pytest.mark.django_db
def test_delete_removes_owned_notification(client: APIClient, user: User) -> None:
    notification = Notification.objects.create(
        user=user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.CAMPAIGN_INVITE,
    )

    response = client.delete(
        reverse("notifications:detail", kwargs={"pk": notification.id})
    )

    assert response.status_code == 204
    assert not Notification.objects.filter(pk=notification.id).exists()


@pytest.mark.django_db
def test_delete_cannot_remove_another_users_notification(
    client: APIClient, other_user: User
) -> None:
    notification = Notification.objects.create(
        user=other_user,
        title="A",
        message="A",
        notification_type=Notification.NotificationType.CAMPAIGN_INVITE,
    )

    response = client.delete(
        reverse("notifications:detail", kwargs={"pk": notification.id})
    )

    assert response.status_code == 404
    assert Notification.objects.filter(pk=notification.id).exists()
