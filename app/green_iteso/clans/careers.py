"""Curated career catalog for institutional clans (#213).

This tuple is the single source of truth for the onboarding picker: a career
outside it is rejected, and its names are reserved so no private clan can take
one and block the onboarding of that career. Extending the catalog is a code
change reviewed like any other; the institutional clan is still created on
the first onboarding for each career.
"""

from __future__ import annotations

import unicodedata

INSTITUTIONAL_CAREERS: tuple[str, ...] = (
    "Ingeniería en Ciberseguridad",
    "Ingeniería en Desarrollo de Software",
    "Ingeniería en Mecatrónica",
    "Ingeniería en Sistemas Computacionales",
)


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
