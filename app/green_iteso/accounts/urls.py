"""Router for the accounts domain, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import EcologicalProfileView, LoginView, TokenRefreshView, UserViewSet

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("profile/me/", EcologicalProfileView.as_view(), name="profile-me"),
    *router.urls,
]
