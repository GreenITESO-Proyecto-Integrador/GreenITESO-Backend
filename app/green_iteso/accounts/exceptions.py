"""Login errors rendered with the SDD error envelope (``{"error": {...}}``)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException


class LoginError(APIException):
    """Base class for login failures; ``code`` and ``message`` fill the envelope."""

    code = "SERVER_ERROR"
    message = "Login failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            detail={"error": {"code": self.code, "message": message or self.message}}
        )


class InvalidIdentityTokenError(LoginError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"
    message = "The Microsoft sign-in could not be verified."


class DomainNotAllowedError(LoginError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "DOMAIN_NOT_ALLOWED"
    message = "Solo se permiten cuentas institucionales @iteso.mx."


class AccountDisabledError(LoginError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"
    message = "Esta cuenta está desactivada."


class AccountConflictError(LoginError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"
    message = "Este correo ya está vinculado a otra cuenta de Microsoft."


class IdentityProviderUnavailableError(LoginError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVER_ERROR"
    message = "The identity provider is temporarily unavailable."
