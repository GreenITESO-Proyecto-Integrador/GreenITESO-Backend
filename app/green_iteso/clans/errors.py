"""Domain errors raised by the clan services (kept apart from services.py)."""

from __future__ import annotations


class DuplicateClanLeaderError(Exception):
    """Raised when a clan would end up with more than one LEADER membership."""


class MembershipClanMismatchError(Exception):
    """Raised when a membership does not belong to the clan it is promoted in."""


class DuplicateClanNameError(ValueError):
    """Raised when ``Clan.name`` is already taken by another alive clan."""


class AlreadyLeadingAClanError(ValueError):
    """Raised when BR-04's single-leadership-per-user rule would be violated."""


class PrivateClanLimitExceededError(ValueError):
    """Raised when BR-04's five-private-clan membership limit would be exceeded."""


class InvalidClanPrivacyError(ValueError):
    """Raised when ``privacy`` is not one of ``Clan.Privacy``'s values."""


class InvitationLimitExceededError(ValueError):
    """Raised when a clan already has the maximum number of active invite codes."""


class InvalidInvitationCodeError(ValueError):
    """Raised for any code that can't be redeemed (#165).

    Deliberately one error for unknown, expired, used, and revoked codes, so
    the response never tells a guesser which codes exist.
    """


class AlreadyClanMemberError(ValueError):
    """Raised when the redeemer already belongs to the invitation's clan."""


class InvitationsNotNeededError(ValueError):
    """Raised when creating a code for a clan anyone can already join directly."""
