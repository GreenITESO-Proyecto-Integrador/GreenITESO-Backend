"""Coverage for the dev-only MockProvider used in local development and CI."""

from __future__ import annotations

import pytest

from green_iteso.accounts.exceptions import InvalidIdentityTokenError
from green_iteso.accounts.identity.mock import MockProvider


def test_plain_email_token() -> None:
    identity = MockProvider().authenticate(
        id_token="mock:student@iteso.mx", access_token=""
    )

    assert identity.email == "student@iteso.mx"


def test_omitted_optional_claims_are_none_not_empty() -> None:
    """None means "not fetched, keep the stored value" (see ExternalIdentity).

    A plain ``mock:<email>`` token only asserts the email; it says nothing
    about department/groups/etc., so those must come back as None rather
    than blank out whatever a previous login already stored.
    """
    identity = MockProvider().authenticate(
        id_token="mock:student@iteso.mx", access_token=""
    )

    assert identity.given_name is None
    assert identity.surname is None
    assert identity.job_title is None
    assert identity.department is None
    assert identity.employee_id is None
    assert identity.group_ids is None


def test_explicitly_empty_claims_are_kept_empty_not_none() -> None:
    """A key present with an empty value is a real answer, not "not fetched"."""
    identity = MockProvider().authenticate(
        id_token='mock:{"email": "ana@iteso.mx", "department": "", "group_ids": []}',
        access_token="",
    )

    assert identity.department == ""
    assert identity.group_ids == ()


def test_json_token_carries_extra_fields() -> None:
    identity = MockProvider().authenticate(
        id_token=(
            'mock:{"email": "staff@iteso.mx", "department": "Sustentabilidad", '
            '"group_ids": ["g1"]}'
        ),
        access_token="",
    )

    assert identity.email == "staff@iteso.mx"
    assert identity.department == "Sustentabilidad"
    assert identity.group_ids == ("g1",)


def test_same_email_yields_the_same_oid() -> None:
    provider = MockProvider()

    first = provider.authenticate(id_token="mock:ana@iteso.mx", access_token="")
    second = provider.authenticate(id_token="mock:ana@iteso.mx", access_token="")

    assert first.oid == second.oid


def test_missing_prefix_is_rejected() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        MockProvider().authenticate(id_token="not-a-mock-token", access_token="")


def test_missing_email_is_rejected() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        MockProvider().authenticate(id_token="mock:{}", access_token="")


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(InvalidIdentityTokenError):
        MockProvider().authenticate(id_token="mock:{not json", access_token="")
