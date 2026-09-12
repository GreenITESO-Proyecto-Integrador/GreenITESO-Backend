"""Feed and community news models for Equipo 3."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class PostType(models.TextChoices):
    OFFICIAL_ANNOUNCEMENT = "OFFICIAL_ANNOUNCEMENT", "Official Announcement"
    COMMUNITY_MILESTONE = "COMMUNITY_MILESTONE", "Community Milestone"
    SHARED_EVIDENCE = "SHARED_EVIDENCE", "Shared Evidence"


class Post(models.Model):
    id = models.BigAutoField(primary_key=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        db_column="author_id",
    )
    post_type = models.CharField(
        max_length=30,
        choices=PostType.choices,
    )
    content = models.TextField()
    image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
    )
    # TODO (Equipo 1): Uncomment when actions.UserBadge is implemented
    # badge_user = models.ForeignKey(
    #     "actions.UserBadge",
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="feed_posts",
    #     db_column="badge_user_id",
    # )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "feed_posts"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    post_type__in=[
                        "OFFICIAL_ANNOUNCEMENT",
                        "COMMUNITY_MILESTONE",
                        "SHARED_EVIDENCE",
                    ]
                ),
                name="feed_post_type_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["created_at", "author"],
                name="feed_post_created_author_idx",
            ),
        ]

    def __str__(self) -> str:
        author_id = self.author_id if self.author_id else "System"
        return f"[{self.post_type}] {self.pk} by {author_id}"

    def clean(self) -> None:
        super().clean()
        if not self.content or not self.content.strip():
            raise ValidationError({"content": "Content cannot be empty."})
        if self.post_type not in PostType.values:
            raise ValidationError(
                {"post_type": f"Invalid post type: {self.post_type}"}
            )
