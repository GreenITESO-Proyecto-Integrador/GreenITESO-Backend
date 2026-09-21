"""HTTP routes that are stable during the backend foundation phase."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def index_view(request: HttpRequest) -> HttpResponse:  # pylint: disable=unused-argument
    """Return the existing service availability response."""
    return HttpResponse("Servidor GreenITESO en funcionamiento.")


urlpatterns = [
    path("", index_view, name="index"),
    path("api/v1/feed/", include("green_iteso.feed.urls")),
    path("api/v1/notifications/", include("green_iteso.notifications.urls")),
    path("api/v1/", include("green_iteso.accounts.urls")),
    path("api/v1/", include("green_iteso.clans.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

if settings.DEBUG:
    urlpatterns.append(path("admin/", admin.site.urls))
