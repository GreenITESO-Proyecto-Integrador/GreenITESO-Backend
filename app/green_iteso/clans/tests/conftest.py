"""Shared test helpers for the clans domain."""

from __future__ import annotations

from green_iteso.accounts.models import ClanMembership, User
from green_iteso.clans.services import create_private_clan


def join_user_to_new_private_clans(user: User, count: int) -> None:
    """Create ``count`` private clans, each led by a fresh user, and join ``user``.

    Used to reach BR-04's five-private-clan membership limit in tests without
    tripping the single-leadership rule: each clan gets its own leader, and
    ``user`` is added as a plain MEMBER.
    """
    for index in range(count):
        leader = User.objects.create_user(
            email=f"lead{index}@iteso.mx", password="local-only"
        )
        clan = create_private_clan(name=f"Clan {index}", created_by=leader)
        ClanMembership.objects.create(
            user=user, clan=clan, role=ClanMembership.MembershipRole.MEMBER
        )
