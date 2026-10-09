"""Router for the accounts domain, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    EcologicalProfileView,
    ImpactTrendView,
    LoginView,
    LogoutView,
    ProfileMetricsView,
    TokenRefreshView,
    UserViewSet,
)

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("profile/me/", EcologicalProfileView.as_view(), name="profile-me"),
    path("profile/me/metrics/", ProfileMetricsView.as_view(), name="profile-metrics"),
    path(
        "profile/me/impact-trend/",
        ImpactTrendView.as_view(),
        name="profile-impact-trend",
    ),
    *router.urls,
]
