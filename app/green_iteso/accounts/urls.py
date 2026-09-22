"""Router for the accounts domain, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import LoginView, UserViewSet

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    *router.urls,
]
