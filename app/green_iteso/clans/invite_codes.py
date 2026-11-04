"""Single-use clan invite codes (#165).

Codes are 8 characters from Crockford's base32 alphabet (no I, L, O, or U),
about 40 bits of entropy, shown as ``XXXX-XXXX``. Only an HMAC keyed by
``SECRET_KEY`` is stored, so a database leak doesn't expose usable codes and
the stored value can still be looked up directly. Python's ``secrets`` is
enough here: TOTP/HOTP libraries model a shared secret for authenticator
apps, not a revocable code handed to one person.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.utils.crypto import salted_hmac

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
INVITATION_TTL = timedelta(hours=48)
# Expired, used, and revoked codes are kept this long for auditing, then
# purged the next time their clan creates a code.
INVITATION_RETENTION = timedelta(days=30)
MAX_ACTIVE_INVITATIONS_PER_CLAN = 20

_HMAC_SALT = "green_iteso.clans.invite_code"
# Characters people commonly type instead of the ones in the alphabet.
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def generate_code() -> str:
    """Return a new random code, formatted for display as ``XXXX-XXXX``."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(raw: str) -> str:
    """Return the canonical form of a typed code, forgiving case and separators."""
    compact = "".join(raw.split()).replace("-", "").upper()
    return compact.translate(_LOOKALIKES)


def hash_code(code: str) -> str:
    """Return the stored lookup value for ``code`` (normalized first)."""
    return salted_hmac(_HMAC_SALT, normalize_code(code), algorithm="sha256").hexdigest()


def code_hint(code: str) -> str:
    """Return the last four characters shown in the leader's list of codes."""
    return normalize_code(code)[-4:]
