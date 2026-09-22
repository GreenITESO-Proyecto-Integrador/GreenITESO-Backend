"""HTTP routes that are stable during the backend foundation phase."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def index_view(request: HttpRequest) -> HttpResponse:  # pylint: disable=unused-argument
    """Return the existing service availability response."""
    return HttpResponse("Servidor GreenITESO en funcionamiento.")


api_v1_patterns = [
    path("", include("green_iteso.campaigns.urls")),
    path("", include("green_iteso.accounts.urls")),
    path("", include("green_iteso.clans.urls")),
    path("feed/", include("green_iteso.feed.urls")),
    path("notifications/", include("green_iteso.notifications.urls")),
]

api_patterns = [
    path("v1/", include(api_v1_patterns)),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

urlpatterns = [
    path("", index_view, name="index"),
    path("api/", include(api_patterns)),
]

if settings.DEBUG:
    urlpatterns.append(path("admin/", admin.site.urls))
