"""Unit and integration test suite for the feed app."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from green_iteso.feed.models import Post, PostType

User = get_user_model()


class PostModelTests(APITestCase):
    """Unit tests for the Post model logic and constraints."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="testuser@iteso.mx",
            password="StrongPassword123!",
            first_name="Test",
        )

    def test_post_creation_and_str_with_author(self) -> None:
        """Validate string representation and persistence with an author."""
        post = Post.objects.create(
            author=self.user,
            post_type=PostType.COMMUNITY_MILESTONE,
            content="Recogimos 10kg de plastico en campus.",
        )
        self.assertIn(str(post.pk), str(post))
        self.assertIn(PostType.COMMUNITY_MILESTONE, str(post))

    def test_post_str_without_author(self) -> None:
        """Ensure system announcements format correctly without author."""
        post = Post.objects.create(
            author=None,
            post_type=PostType.OFFICIAL_ANNOUNCEMENT,
            content="Aviso institucional general.",
        )
        self.assertIn("System", str(post))

    def test_clean_rejects_empty_content(self) -> None:
        """Verify model validation fails if content contains only whitespace."""
        post = Post(
            author=self.user,
            post_type=PostType.SHARED_EVIDENCE,
            content="   ",
        )
        with self.assertRaises(ValidationError):
            post.clean()

    def test_save_rejects_empty_content_at_persistence(self) -> None:
        """Verify save() invokes full_clean and prevents persisting empty content."""
        post = Post(
            author=self.user,
            post_type=PostType.SHARED_EVIDENCE,
            content="   ",
        )
        with self.assertRaises(ValidationError):
            post.save()

    def test_clean_rejects_invalid_post_type(self) -> None:
        """Verify model validation rejects unlisted enum types."""
        post = Post(
            author=self.user,
            post_type="INVALID_CHOICE",
            content="Contenido valido",
        )
        with self.assertRaises(ValidationError):
            post.clean()


class PostAPITests(APITestCase):  # pylint: disable=too-many-ancestors
    """Integration tests for feed REST API endpoints."""

    def setUp(self) -> None:
        self.author = User.objects.create_user(
            email="author@iteso.mx",
            password="StrongPassword123!",
            first_name="Author",
        )
        self.other_user = User.objects.create_user(
            email="other@iteso.mx",
            password="StrongPassword123!",
            first_name="Other",
        )
        self.post = Post.objects.create(
            author=self.author,
            post_type=PostType.SHARED_EVIDENCE,
            content="Evidencia inicial de prueba.",
        )
        self.base_url = "/api/v1/feed/"
        self.detail_url = f"/api/v1/feed/{self.post.pk}/"

    def test_list_feed_posts_unauthenticated(self) -> None:
        """Ensure the feed listing is publicly accessible."""
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_single_post(self) -> None:
        """Ensure individual post details can be fetched."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.post.pk)

    def test_author_data_excludes_sensitive_email(self) -> None:
        """Verify that author payload omits email addresses to prevent harvesting."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        author_data = response.data.get("author")
        self.assertIsNotNone(author_data)
        self.assertNotIn("email", author_data)

    def test_create_post_authenticated(self) -> None:
        """Ensure logged-in users can successfully publish posts."""
        self.client.force_authenticate(user=self.author)
        payload = {
            "post_type": PostType.COMMUNITY_MILESTONE,
            "content": "Publicacion creada mediante API.",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["post_type"], PostType.COMMUNITY_MILESTONE)

    def test_create_post_unauthenticated_forbidden(self) -> None:
        """Ensure unauthenticated users receive 403 Forbidden."""
        payload = {
            "post_type": PostType.OFFICIAL_ANNOUNCEMENT,
            "content": "Intento anonimo.",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_post_validation_error(self) -> None:
        """Ensure serializer rejects payloads with empty content."""
        self.client.force_authenticate(user=self.author)
        payload = {
            "post_type": PostType.COMMUNITY_MILESTONE,
            "content": "   ",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_post_as_author(self) -> None:
        """Ensure authors can edit their own content."""
        self.client.force_authenticate(user=self.author)
        payload = {"content": "Contenido modificado por autor."}
        response = self.client.patch(self.detail_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.post.refresh_from_db()
        self.assertEqual(self.post.content, "Contenido modificado por autor.")

    def test_update_post_as_non_author_forbidden(self) -> None:
        """Ensure users cannot edit posts created by others."""
        self.client.force_authenticate(user=self.other_user)
        payload = {"content": "Intento de edicion no autorizada."}
        response = self.client.patch(self.detail_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_post_as_author(self) -> None:
        """Ensure authors can delete their own posts."""
        self.client.force_authenticate(user=self.author)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Post.objects.filter(pk=self.post.pk).exists())

    def test_delete_post_as_non_author_forbidden(self) -> None:
        """Ensure users cannot delete posts created by others."""
        self.client.force_authenticate(user=self.other_user)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_post_as_staff_allowed(self) -> None:
        """Ensure staff members can moderate and delete posts from other users."""
        staff_user = User.objects.create_user(
            email="staff@iteso.mx",
            password="StrongPassword123!",
            is_staff=True,
        )
        self.client.force_authenticate(user=staff_user)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Post.objects.filter(pk=self.post.pk).exists())

    def test_relative_time_formats(self) -> None:
        """Verify serializer relative time formats in Spanish."""
        post_old = Post.objects.create(
            author=self.author,
            post_type=PostType.COMMUNITY_MILESTONE,
            content="Post antiguo.",
        )
        Post.objects.filter(pk=post_old.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )

        res = self.client.get(f"/api/v1/feed/{post_old.pk}/")
        self.assertIn("hace", res.data["relative_time"])
