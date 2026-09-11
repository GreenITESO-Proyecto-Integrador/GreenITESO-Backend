"""WSGI config for GreenITESO."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "green_iteso.settings")
application = get_wsgi_application()
