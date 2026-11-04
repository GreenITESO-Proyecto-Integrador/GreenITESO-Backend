"""Rate limits for clan endpoints."""

from __future__ import annotations

from rest_framework.throttling import UserRateThrottle


class ClanInviteRedeemThrottle(UserRateThrottle):
    """Limit invite-code redemptions per user (#165).

    The rate comes from ``DEFAULT_THROTTLE_RATES["clan_invite_redeem"]``.
    No shared ``CACHES`` backend is configured, so the counter lives in each
    process's local memory: with several workers the effective limit is the
    rate times ``WEB_CONCURRENCY``. It is a second layer behind the code's
    entropy, not the only defense.
    """

    scope = "clan_invite_redeem"
