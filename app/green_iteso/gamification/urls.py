"""Routes for the gamification domain."""

from __future__ import annotations

from django.urls import path

from .views import BadgeCatalogView, ClanRankingView, UserRankingView

urlpatterns = [
    path("users/", UserRankingView.as_view(), name="ranking-users"),
    path("badges/", BadgeCatalogView.as_view(), name="badge-catalog"),
    path("", ClanRankingView.as_view(), name="ranking-clans"),
]
