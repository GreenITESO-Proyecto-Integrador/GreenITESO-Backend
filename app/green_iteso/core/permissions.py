"""Reusable DRF permission classes combining global and clan-contextual roles."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan, ClanMembership, User

from .roles import ClanRole, GlobalRole


class IsAdmin(BasePermission):
    """Allow access only to callers with the global ADMIN role."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == GlobalRole.ADMIN)


class IsSelfOrAdmin(BasePermission):
    """Allow the resource's owner or a global ADMIN; deny everyone else."""

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role == GlobalRole.ADMIN:
            return True
        owner = obj if isinstance(obj, User) else getattr(obj, "user", None)
        return owner == user


class IsClanLeader(BasePermission):
    """Allow a clan's LEADER, or a global ADMIN, to manage that clan.

    ``obj`` must be a ``Clan`` or expose a ``.clan`` attribute.
    """

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role == GlobalRole.ADMIN:
            return True
        clan = obj if isinstance(obj, Clan) else getattr(obj, "clan", None)
        if clan is None:
            return False
        return ClanMembership.objects.filter(
            user=user, clan=clan, role=ClanRole.LEADER
        ).exists()
