import sys
from typing import Any

from django.conf import settings
from django.core.management import execute_from_command_line
from django.http import HttpRequest, HttpResponse
from django.urls import path

# Configuración mínima en memoria
if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="dev-insecure-secret-key-change-in-production",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
    )


# Vista básica con tipado estricto
def index_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse("Servidor GreenITESO en funcionamiento.")


# Enrutamiento
urlpatterns: list[Any] = [
    path("", index_view, name="index"),
]

if __name__ == "__main__":
    execute_from_command_line(sys.argv)
