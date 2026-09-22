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
