"""Reviewed Neon endpoint identities for the three deployed environments.

The values mirror the read-only Infra inventory in
``config/neon-endpoints.tsv`` (including the ``c-4`` routing component).
They are identifiers, not credentials.
"""

from __future__ import annotations

CANONICAL_NEON_ENDPOINTS: dict[str, dict[str, str]] = {
    "dev": {
        "direct": "ep-lively-brook-ax4n0pys.c-4.us-east-2.aws.neon.tech",
        "pooled": "ep-lively-brook-ax4n0pys-pooler.c-4.us-east-2.aws.neon.tech",
    },
    "staging": {
        "direct": "ep-withered-cake-axk8vlfi.c-4.us-east-2.aws.neon.tech",
        "pooled": "ep-withered-cake-axk8vlfi-pooler.c-4.us-east-2.aws.neon.tech",
    },
    "production": {
        "direct": "ep-old-salad-axvsz82z.c-4.us-east-2.aws.neon.tech",
        "pooled": "ep-old-salad-axvsz82z-pooler.c-4.us-east-2.aws.neon.tech",
    },
}


def canonical_neon_host(environment: str, *, pooled: bool) -> str:
    """Return the reviewed host for an environment and connection role."""
    try:
        return CANONICAL_NEON_ENDPOINTS[environment]["pooled" if pooled else "direct"]
    except KeyError as error:
        raise RuntimeError(
            f"No canonical Neon endpoint is recorded for DJANGO_ENV={environment!r}."
        ) from error
