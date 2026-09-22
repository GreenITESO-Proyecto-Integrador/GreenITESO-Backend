"""Development-only provider that trusts a ``mock:`` token without calling Microsoft."""

from __future__ import annotations

import json
import uuid

from green_iteso.accounts.exceptions import InvalidIdentityTokenError

from .base import ExternalIdentity

_PREFIX = "mock:"
_NAMESPACE = uuid.UUID("6f1c2f0e-8a54-4a3e-9f0b-2d7e5b0c9a11")
_MOCK_TENANT = "00000000-0000-0000-0000-000000000000"


class MockProvider:
    """Accepts ``mock:<email>`` or ``mock:{"email": ..., "department": ...}``.

    The object id is derived from the email so repeated logins hit the same user.
    Settings refuse this provider unless ``DJANGO_ENV=dev`` and the process is
    not deployed.
    """

    def authenticate(  # pylint: disable=unused-argument
        self, *, id_token: str, access_token: str
    ) -> ExternalIdentity:
        """Ignore ``access_token``: mock mode never calls Graph."""
        if not id_token.startswith(_PREFIX):
            raise InvalidIdentityTokenError(
                "Mock mode expects an id_token like mock:<email>."
            )
        payload = id_token.removeprefix(_PREFIX).strip()
        if payload.startswith("{"):
            try:
                claims = json.loads(payload)
            except ValueError as exc:
                raise InvalidIdentityTokenError(
                    "The mock token is not valid JSON."
                ) from exc
            if not isinstance(claims, dict):
                raise InvalidIdentityTokenError("The mock token must be a JSON object.")
        else:
            claims = {"email": payload}
        email = str(claims.get("email", "")).strip()
        if not email:
            raise InvalidIdentityTokenError("The mock token needs an email.")
        groups = claims.get("group_ids")
        return ExternalIdentity(
            oid=str(uuid.uuid5(_NAMESPACE, email.lower())),
            tenant_id=_MOCK_TENANT,
            email=email,
            given_name=str(claims.get("given_name", "")),
            surname=str(claims.get("surname", "")),
            job_title=str(claims.get("job_title", "")),
            department=str(claims.get("department", "")),
            employee_id=str(claims.get("employee_id", "")),
            group_ids=tuple(str(g) for g in groups) if isinstance(groups, list) else (),
        )
