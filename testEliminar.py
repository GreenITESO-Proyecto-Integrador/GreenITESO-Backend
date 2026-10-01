import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "green_iteso.settings")
os.environ.setdefault("DJANGO_ENV", "dev")

django.setup()

from green_iteso.accounts.models import User, UserProfile
from green_iteso.accounts.services import ensure_profile

user, _ = User.objects.get_or_create(email="prueba@iteso.mx")
profile = ensure_profile(user)

print(f"Puntos antes: total={profile.total_points}, disponibles={profile.available_points}")

profile.total_points = 250
profile.available_points = 250
profile.save(update_fields=["total_points", "available_points"])

profile.refresh_from_db()
print(f"Puntos en BD: total={profile.total_points}, disponibles={profile.available_points}")
