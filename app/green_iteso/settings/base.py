"""Shared Django settings and strict environment parsing."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from dotenv import load_dotenv

from green_iteso.security import redact_database_url
from green_iteso.settings.neon_endpoints import canonical_neon_host

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR.parent / ".env", override=False)


def required(name: str) -> str:
    """Read a non-empty environment variable or fail with an actionable error."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is missing or empty for "
            f"DJANGO_ENV={os.environ.get('DJANGO_ENV', '<unset>')!r}."
        )
    return value


def csv_setting(name: str) -> list[str]:
    """Read a comma-separated setting while rejecting empty host entries."""
    return [entry.strip() for entry in required(name).split(",") if entry.strip()]


def required_bool(name: str) -> bool:
    """Read an explicit boolean environment value."""
    value = required(name).lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be exactly true or false; received {value!r}.")
    return value == "true"


def database_from_url(
    url: str,
    *,
    require_ssl: bool = False,
    expected_pooled: bool | None = None,
) -> dict[str, object]:
    """Convert a PostgreSQL URL to Django's database configuration."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(
            f"DATABASE_URL must use postgres:// or postgresql://; received "
            f"{redact_database_url(url)}"
        )
    if not parsed.hostname or not parsed.path.lstrip("/"):
        raise RuntimeError("DATABASE_URL must include a host and database name.")
    is_pooled = "-pooler" in parsed.hostname.lower()
    if expected_pooled is True and not is_pooled:
        raise RuntimeError("Deployed app DATABASE_URL must use the Neon pooler host.")
    if expected_pooled is False and is_pooled:
        raise RuntimeError(
            "Direct migration DATABASE_URL must not use the Neon pooler host."
        )
    query = parse_qs(parsed.query)
    options: dict[str, str] = {}
    sslmode = query.get("sslmode", [""])[0]
    if require_ssl and sslmode != "verify-full":
        raise RuntimeError("Deployed PostgreSQL URLs must include sslmode=verify-full.")
    if require_ssl:
        deployed_environment = os.environ.get("DJANGO_ENV", "")
        expected_host = canonical_neon_host(deployed_environment, pooled=is_pooled)
        if parsed.hostname.lower() != expected_host:
            connection_kind = "pooled" if is_pooled else "direct"
            raise RuntimeError(
                f"DATABASE_URL host does not match the canonical {deployed_environment} "
                f"{connection_kind} Neon endpoint."
            )
    if sslmode:
        options["sslmode"] = sslmode
    if sslmode == "verify-full":
        # The psycopg binary wheel's OpenSSL paths may not match Debian's
        # system store. Use the installed bundle explicitly in our image.
        bundle = Path("/etc/ssl/certs/ca-certificates.crt")
        default_ca = str(bundle) if bundle.is_file() else "system"
        options["sslrootcert"] = query.get("sslrootcert", [default_ca])[0] or default_ca
    if query.get("channel_binding", [""])[0]:
        options["channel_binding"] = query["channel_binding"][0]

    database: dict[str, object] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or "5432"),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "DISABLE_SERVER_SIDE_CURSORS": True,
    }
    if options:
        database["OPTIONS"] = options
    return database


SECRET_KEY = required("DJANGO_SECRET_KEY")
DEPLOYED = required_bool("DJANGO_DEPLOYED")
CONNECTION_ROLE = required("DJANGO_CONNECTION_ROLE")
if CONNECTION_ROLE not in {"app", "direct"}:
    raise RuntimeError("DJANGO_CONNECTION_ROLE must be exactly app or direct.")
DEBUG = False
ALLOWED_HOSTS = csv_setting("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "green_iteso.accounts",
    "green_iteso.clans",
    "green_iteso.actions",
    "green_iteso.campaigns",
    "green_iteso.feed",
    "green_iteso.notifications",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication"
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {"auth_login": "10/min"},
}

# Provisional lifetimes from the SDD; T2-11 owns the final values.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Microsoft Entra ID login (T2-10). ``mock`` skips Microsoft entirely, so it is
# only accepted on a local, non-deployed dev process. Tenant and client ids are
# checked when the first login is attempted, so releases that only run
# migrations do not need them.
MICROSOFT_AUTH_MODE = os.environ.get("MICROSOFT_AUTH_MODE", "entra").strip().lower()
if MICROSOFT_AUTH_MODE not in {"entra", "mock"}:
    raise RuntimeError("MICROSOFT_AUTH_MODE must be exactly entra or mock.")
if MICROSOFT_AUTH_MODE == "mock" and (
    os.environ.get("DJANGO_ENV") != "dev" or DEPLOYED
):
    raise RuntimeError(
        "MICROSOFT_AUTH_MODE=mock is only allowed with DJANGO_ENV=dev and "
        "DJANGO_DEPLOYED=false."
    )
MICROSOFT_TENANT_ID = os.environ.get("MICROSOFT_TENANT_ID", "").strip()
MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
ALLOWED_EMAIL_DOMAIN = (
    os.environ.get("ALLOWED_EMAIL_DOMAIN", "iteso.mx").strip().lower()
)

if DEPLOYED:
    # TLS terminates at the load balancer, which forwards X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

SPECTACULAR_SETTINGS = {
    "TITLE": "GreenITESO API",
    "DESCRIPTION": "REST API for the GreenITESO backend.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "green_iteso.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "green_iteso.wsgi.application"
ASGI_APPLICATION = "green_iteso.asgi.application"

DATABASE_URL = required("DATABASE_URL")
DATABASES = {
    "default": database_from_url(
        DATABASE_URL,
        require_ssl=DEPLOYED,
        expected_pooled=DEPLOYED and CONNECTION_ROLE == "app",
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
