"""Small HTTP middleware shared by the local development API."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers


class DevelopmentCorsMiddleware:
    """Allow the configured frontend origin and answer browser preflight requests."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        origin = request.headers.get("Origin", "")
        allowed_origins = settings.CORS_ALLOWED_ORIGINS

        if (
            request.method == "OPTIONS"
            and origin in allowed_origins
            and request.headers.get("Access-Control-Request-Method")
        ):
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        patch_vary_headers(response, ["Origin"])
        if origin in allowed_origins:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )

        return response
