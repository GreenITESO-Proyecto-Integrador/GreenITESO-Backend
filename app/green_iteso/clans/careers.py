"""Curated career catalog for institutional clans (#213).

This tuple is the single source of truth for the onboarding picker: a career
outside it is rejected, and its names are reserved so no private clan can take
one and block the onboarding of that career. Extending the catalog is a code
change reviewed like any other; the institutional clan is still created on
the first onboarding for each career.
"""

from __future__ import annotations

import unicodedata

from green_iteso.accounts.models import User

from .errors import (
    CareerNotAllowedForRoleError,
    ReservedClanNameError,
    UnknownCareerError,
)

STAFF_CAREER = "Staff ITESO"

INSTITUTIONAL_CAREERS: tuple[str, ...] = (
    "Ingeniería en Ciberseguridad",
    "Ingeniería en Desarrollo de Software",
    "Ingeniería en Mecatrónica",
    "Ingeniería en Sistemas Computacionales",
    # Not a career: the institutional clan for staff, who study none.
    STAFF_CAREER,
)

STAFF_ONLY_CAREERS: frozenset[str] = frozenset({STAFF_CAREER})
"""Catalog entries only ``User.Role.STAFF`` accounts can pick in onboarding."""


def normalize_name(name: str) -> str:
    """Fold case, accents and repeated spaces so near-identical names compare equal."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


_CATALOG_BY_KEY = {normalize_name(career): career for career in INSTITUTIONAL_CAREERS}


def canonical_career(value: str) -> str | None:
    """Return the catalog spelling of ``value``, or ``None`` if it is not a career."""
    return _CATALOG_BY_KEY.get(normalize_name(value))


def is_reserved_clan_name(name: str) -> bool:
    """Whether ``name`` belongs to a catalog career and so to an institutional clan."""
    return canonical_career(name) is not None


def careers_for_role(role: str) -> list[str]:
    """Return the sorted catalog entries a user with ``role`` may pick."""
    return sorted(
        career
        for career in INSTITUTIONAL_CAREERS
        if career not in STAFF_ONLY_CAREERS or role == User.Role.STAFF
    )


def require_career(value: str, *, role: str) -> str:
    """Return the catalog spelling of ``value`` for onboarding by a ``role`` user.

    Raises:
        UnknownCareerError: If ``value`` is not in the catalog.
        CareerNotAllowedForRoleError: If the career is reserved for another role.
    """
    career = canonical_career(value)
    if career is None:
        raise UnknownCareerError(f"'{value.strip()}' is not in the career catalog.")
    if career in STAFF_ONLY_CAREERS and role != User.Role.STAFF:
        raise CareerNotAllowedForRoleError(
            f"'{career}' is only available to staff accounts."
        )
    return career


def ensure_name_not_reserved(name: str) -> None:
    """Refuse a private clan name that belongs to a catalog career.

    Such a clan would make every onboarding into that career fail on the
    unique clan name.

    Raises:
        ReservedClanNameError: If ``name`` matches a catalog career.
    """
    if is_reserved_clan_name(name):
        raise ReservedClanNameError(
            f"'{name.strip()}' is reserved for an institutional clan."
        )
