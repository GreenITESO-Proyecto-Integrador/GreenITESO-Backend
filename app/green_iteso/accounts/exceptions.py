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
    """Raise with an explicit ``message`` naming the configured domain.

    ``ALLOWED_EMAIL_DOMAIN`` is configurable, so this default is only a
    fallback for callers that don't build the domain-specific message
    themselves (``services.login_with_microsoft`` always does).
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "DOMAIN_NOT_ALLOWED"
    message = "Solo se permiten cuentas institucionales del dominio configurado."


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


class RequestValidationError(LoginError):
    """Wraps DRF's default field-error shape in the documented envelope.

    ``is_valid(raise_exception=True)`` raises a plain
    ``rest_framework.exceptions.ValidationError`` with DRF's own
    ``{"field": [...]}`` shape, not this module's envelope. There is no
    project-wide ``EXCEPTION_HANDLER`` to normalize that (E1/E3 don't use
    this envelope yet), so ``LoginView`` translates it locally; see
    ``LoginView.handle_exception``.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "VALIDATION_ERROR"
    message = "The request body is invalid."
