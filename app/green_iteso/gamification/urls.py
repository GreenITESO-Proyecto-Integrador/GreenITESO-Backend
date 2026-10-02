"""Routes for the gamification domain, mounted under /api/v1/rankings/."""

from __future__ import annotations

from django.urls import path

from .views import UserRankingView

urlpatterns = [
    path("users/", UserRankingView.as_view(), name="ranking-users"),
]
