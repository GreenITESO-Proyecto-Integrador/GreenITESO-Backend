"""Coverage for the local development CORS middleware."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from green_iteso.core.middleware import DevelopmentCorsMiddleware


def _response_with_vary_cookie(request: HttpRequest) -> HttpResponse:
    response = HttpResponse("ok")
    response["Vary"] = "Cookie"
    return response


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"])
def test_allowed_origin_adds_cors_headers() -> None:
    request = RequestFactory().get(
        "/api/v1/users/me/",
        HTTP_ORIGIN="http://localhost:3000",
    )

    response = DevelopmentCorsMiddleware(_response_with_vary_cookie)(request)

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert response["Access-Control-Allow-Headers"] == "Authorization, Content-Type"
    assert response["Access-Control-Allow-Methods"] == (
        "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    )


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"])
def test_disallowed_origin_passes_through_without_cors_headers() -> None:
    request = RequestFactory().get(
        "/api/v1/users/me/",
        HTTP_ORIGIN="https://untrusted.example.com",
    )

    response = DevelopmentCorsMiddleware(_response_with_vary_cookie)(request)

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response
    assert "Access-Control-Allow-Headers" not in response
    assert "Access-Control-Allow-Methods" not in response
    assert response["Vary"] == "Cookie"


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"])
def test_allowed_preflight_returns_no_content_without_calling_view() -> None:
    called = False

    def get_response(request: HttpRequest) -> HttpResponse:
        nonlocal called
        called = True
        return HttpResponse("should not be called")

    request = RequestFactory().options(
        "/api/v1/users/me/",
        HTTP_ORIGIN="http://localhost:3000",
    )

    response = DevelopmentCorsMiddleware(get_response)(request)

    assert response.status_code == 204
    assert response.content == b""
    assert called is False
    assert response["Access-Control-Allow-Origin"] == "http://localhost:3000"


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"])
def test_cors_origin_is_added_to_existing_vary_header() -> None:
    request = RequestFactory().get(
        "/api/v1/users/me/",
        HTTP_ORIGIN="http://localhost:3000",
    )

    response = DevelopmentCorsMiddleware(_response_with_vary_cookie)(request)

    assert response["Vary"] == "Cookie, Origin"
