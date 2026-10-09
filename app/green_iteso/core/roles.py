"""Reusable role catalog distinguishing global and clan-contextual roles."""

from __future__ import annotations

from green_iteso.accounts.models import ClanMembership, User

GlobalRole = User.Role
"""Global application role (User.role): STUDENT / STAFF / ADMIN."""

ClanRole = ClanMembership.MembershipRole
"""Contextual clan role (ClanMembership.role): LEADER / MEMBER, independent of GlobalRole."""
