"""Provider-neutral shapes for a verified external identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ExternalIdentity:
    """What the identity provider vouched for and what Graph added.

    ``None`` on an optional field means "not fetched" (for example Graph was
    unreachable), so callers keep whatever was stored before. An empty string or
    tuple means the provider answered and the value is genuinely empty.
    """

    oid: str
    tenant_id: str
    email: str
    given_name: str | None = None
    surname: str | None = None
    job_title: str | None = None
    department: str | None = None
    employee_id: str | None = None
    group_ids: tuple[str, ...] | None = field(default=None)


class IdentityProvider(Protocol):
    """Turns the tokens the SPA obtained into a verified ``ExternalIdentity``."""

    def authenticate(self, *, id_token: str, access_token: str) -> ExternalIdentity:
        """Raise ``LoginError`` subclasses when the tokens cannot be trusted."""
        ...
