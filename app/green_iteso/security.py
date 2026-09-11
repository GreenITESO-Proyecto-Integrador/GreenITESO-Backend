"""Small helpers for handling connection information safely."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def redact_database_url(value: str) -> str:
    """Redact credentials in a database URL before it reaches logs or errors."""
    try:
        parsed = urlsplit(value)
        password = parsed.password
        port = parsed.port
    except ValueError:
        return "<redacted database URL>"
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path:
        return "<redacted database URL>"
    user = parsed.username or ""
    host = parsed.hostname or ""
    port_suffix = f":{port}" if port else ""
    netloc = f"{user}:***@{host}{port_suffix}" if password is not None else f"{user}@{host}{port_suffix}"
    sensitive_keys = {"password", "pass", "secret", "token", "api_key", "apikey"}
    query = urlencode(
        [
            (key, "***" if key.lower() in sensitive_keys else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
