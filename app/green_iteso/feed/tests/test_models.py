"""Unit tests for the Post model logic and constraints."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from green_iteso.feed.models import Post, PostType
from green_iteso.gamification.models import Badge, UserBadge

User = get_user_model()

class PostModelTests(APITestCase):
    """Unit tests for the Post model logic and constraints."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="testuser@iteso.mx",
            password="StrongPassword123!",
            first_name="Test",
        )
        self.other_user = User.objects.create_user(
            email="otheruser@iteso.mx",
            password="StrongPassword123!",
            first_name="Other",
        )
        self.badge = Badge.objects.create(
            name="Reciclador Pro",
            description="Reciclaste 50 items",
            icon_name="recycle",
        )
        self.user_badge = UserBadge.objects.create(
            user=self.user,
            badge=self.badge,
        )

    def test_post_creation_and_str_with_author(self) -> None:
        post = Post.objects.create(
            author=self.user,
            post_type=PostType.COMMUNITY_MILESTONE,
            content="Recogimos 10kg de plastico en campus.",
        )
        self.assertIn(str(post.pk), str(post))
        self.assertIn(PostType.COMMUNITY_MILESTONE, str(post))

    def test_post_str_without_author(self) -> None:
        post = Post.objects.create(
            author=None,
            post_type=PostType.OFFICIAL_ANNOUNCEMENT,
            content="Aviso institucional general.",
        )
        self.assertIn("System", str(post))

    def test_clean_rejects_empty_content(self) -> None:
        post = Post(
            author=self.user,
            post_type=PostType.SHARED_EVIDENCE,
            content="   ",
        )
        with self.assertRaises(ValidationError):
            post.clean()

    def test_save_rejects_empty_content_at_persistence(self) -> None:
        post = Post(
            author=self.user,
            post_type=PostType.SHARED_EVIDENCE,
            content="   ",
        )
        with self.assertRaises(ValidationError):
            post.save()

    def test_clean_rejects_invalid_post_type(self) -> None:
        post = Post(
            author=self.user,
            post_type="INVALID_CHOICE",
            content="Contenido valido",
        )
        with self.assertRaises(ValidationError):
            post.clean()

    def test_clean_rejects_badge_belonging_to_another_user(self) -> None:
        post = Post(
            author=self.other_user,
            badge_user=self.user_badge,
            post_type=PostType.COMMUNITY_MILESTONE,
            content="Intento de usar medalla ajena",
        )
        with self.assertRaises(ValidationError):
            post.clean()