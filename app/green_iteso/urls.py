"""HTTP routes that are stable during the backend foundation phase."""

from django.http import HttpRequest, HttpResponse
from django.urls import path


def index_view(request: HttpRequest) -> HttpResponse:  # pylint: disable=unused-argument
    """Return the existing service availability response."""
    return HttpResponse("Servidor GreenITESO en funcionamiento.")


urlpatterns = [
    path("", index_view, name="index"),
]
