import os
import sys

from django.core.management import execute_from_command_line

from green_iteso.urls import index_view, urlpatterns

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "green_iteso.settings")

__all__ = ["index_view", "urlpatterns"]

if __name__ == "__main__":
    execute_from_command_line(sys.argv)
