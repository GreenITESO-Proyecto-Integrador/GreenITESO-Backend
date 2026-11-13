# pylint: disable=no-member,too-many-instance-attributes, too-many-public-methods,line-too-long
"""Integration tests for feed REST API endpoints."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from green_iteso.feed.models import Post, PostType
from green_iteso.gamification.models import Badge, UserBadge

User = get_user_model()


class PostAPITests(APITestCase):
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
        self.badge = Badge.objects.create(
            name="Plantador de Arboles",
            description="Participo en reforestacion",
            icon_name="tree",
        )
        self.author_badge = UserBadge.objects.create(
            user=self.author,
            badge=self.badge,
        )
        self.other_badge = UserBadge.objects.create(
            user=self.other_user,
            badge=self.badge,
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

    def test_create_post_with_badge_success(self) -> None:
        """Ensure user can create a post attaching their own badge."""
        self.client.force_authenticate(user=self.author)
        payload = {
            "post_type": PostType.COMMUNITY_MILESTONE,
            "content": "Publicacion con mi nueva medalla.",
            "badge_user": str(self.author_badge.id),
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(response.data["badge_info"])
        self.assertEqual(response.data["badge_info"]["name"], "Plantador de Arboles")
        self.assertEqual(response.data["badge_info"]["icon_name"], "tree")

    def test_create_post_with_unowned_badge_rejected(self) -> None:
        """Ensure attaching someone else's badge returns 400 Bad Request."""
        self.client.force_authenticate(user=self.author)
        payload = {
            "post_type": PostType.COMMUNITY_MILESTONE,
            "content": "Intentando presumir medalla que no es mia.",
            "badge_user": str(self.other_badge.id),
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("badge_user", response.data)

    def test_create_post_unauthenticated_unauthorized(self) -> None:
        """Ensure unauthenticated users receive 401 Unauthorized."""
        payload = {
            "post_type": PostType.OFFICIAL_ANNOUNCEMENT,
            "content": "Intento anonimo.",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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

    def test_delete_post_as_admin_allowed(self) -> None:
        """Ensure admin users can moderate and delete posts from other users."""
        admin_user = User.objects.create_user(
            email="admin@iteso.mx",
            password="StrongPassword123!",
            role="ADMIN",
        )
        self.client.force_authenticate(user=admin_user)
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

    def test_feed_list_query_count_does_not_grow_with_badges(self) -> None:
        """Ensure listing posts does not trigger N+1 queries when posts have badges."""
        for i in range(5):
            Post.objects.create(
                author=self.author,
                content=f"Post con insignia {i}",
                post_type=PostType.SHARED_EVIDENCE,
                badge_user=self.author_badge,
            )
        self.client.force_authenticate(user=self.author)
        with self.assertNumQueries(2):
            response = self.client.get(self.base_url)
            self.assertEqual(response.status_code, 200)

    def test_create_official_announcement_as_non_admin_forbidden(self) -> None:
        """Ensure standard users cannot create official announcements."""
        self.client.force_authenticate(user=self.author)
        payload = {
            "post_type": PostType.OFFICIAL_ANNOUNCEMENT,
            "content": "Intento de anuncio oficial falso.",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_official_announcement_as_non_admin_forbidden(self) -> None:
        """Ensure standard users cannot edit an existing post to make it official."""
        self.client.force_authenticate(user=self.author)
        payload = {"post_type": PostType.OFFICIAL_ANNOUNCEMENT}
        response = self.client.patch(self.detail_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_official_announcement_as_admin_allowed(self) -> None:
        """Ensure admins can successfully create official announcements."""
        admin_user = User.objects.create_user(
            email="admin_creator@iteso.mx",
            password="StrongPassword123!",
            role="ADMIN",
        )
        self.client.force_authenticate(user=admin_user)
        payload = {
            "post_type": PostType.OFFICIAL_ANNOUNCEMENT,
            "content": "Aviso oficial real.",
        }
        response = self.client.post(self.base_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_delete_post_as_staff_forbidden(self) -> None:
        """Ensure users with only is_staff=True (not ADMIN role) cannot delete other users' posts."""
        staff_user = User.objects.create_user(
            email="staff_no_admin@iteso.mx",
            password="StrongPassword123!",
            is_staff=True,
        )
        self.client.force_authenticate(user=staff_user)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_post_as_admin_forbidden(self) -> None:
        """Ensure admins cannot edit posts created by other users."""
        admin_user = User.objects.create_user(
            email="admin_editor@iteso.mx",
            password="StrongPassword123!",
            role="ADMIN",
        )
        self.client.force_authenticate(user=admin_user)
        payload = {"content": "Admin intentando editar post ajeno."}
        response = self.client.patch(self.detail_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_degraded_admin_cannot_change_content(self) -> None:
        """Ensure an admin who loses their role cannot edit the content of their official announcement."""
        admin_user = User.objects.create_user(
            email="degraded_content@iteso.mx",
            password="StrongPassword123!",
            role="ADMIN",
        )
        official_post = Post.objects.create(
            author=admin_user,
            post_type=PostType.OFFICIAL_ANNOUNCEMENT,
            content="Contenido original",
        )

        # Degradar al administrador
        admin_user.role = "STUDENT"
        admin_user.save()

        self.client.force_authenticate(user=admin_user)
        detail_url = f"{self.base_url}{official_post.pk}/"
        response = self.client.patch(detail_url, data={"content": "Contenido hackeado"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        official_post.refresh_from_db()
        self.assertEqual(official_post.content, "Contenido original")
        self.assertEqual(official_post.post_type, PostType.OFFICIAL_ANNOUNCEMENT)

    def test_degraded_admin_cannot_change_post_type(self) -> None:
        """Ensure an admin who loses their role cannot change the type of their official announcement."""
        admin_user = User.objects.create_user(
            email="degraded_type@iteso.mx",
            password="StrongPassword123!",
            role="ADMIN",
        )
        official_post = Post.objects.create(
            author=admin_user,
            post_type=PostType.OFFICIAL_ANNOUNCEMENT,
            content="Contenido original",
        )

        # Degradar al administrador
        admin_user.role = "STUDENT"
        admin_user.save()

        self.client.force_authenticate(user=admin_user)
        detail_url = f"{self.base_url}{official_post.pk}/"
        response = self.client.patch(
            detail_url,
            data={
                "post_type": PostType.SHARED_EVIDENCE,
                "content": "Contenido malicioso",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        official_post.refresh_from_db()
        self.assertEqual(official_post.post_type, PostType.OFFICIAL_ANNOUNCEMENT)
        self.assertEqual(official_post.content, "Contenido original")
