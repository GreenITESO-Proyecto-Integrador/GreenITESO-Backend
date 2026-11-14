from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import migrations

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor

DEMO_COSMETICS = [
    {
        "key": "frame_halloween",
        "name": "Marco noche de brujas",
        "description": (
            "un marco que causara terror y devoción a cualquiera que lo mire, "
            "contiene una variedad de tenebrosos elementos como lo son brujas, "
            "gatos, fantasmas y más. Celebra estas festividades a lo grande "
            "decorando tu perfil."
        ),
        "category": "FRAME",
        "points_cost": 20,
        "image_url": (
            "https://staticfiles.warthunder.com/upload/image/0_2025/10_October/"
            "Halloween/halloween_frame_2_9063910c08949c31b92d29549b7fd4dc.png"
        ),
        "is_active": True,
    },
    {
        "key": "frame_war",
        "name": "Marco de guerra",
        "description": (
            "demuestra quien es el soldado ecologico mayor. Marco muy rudo, "
            "con tonos metálicos y cadenas oxidadas. Solo apto para gente muy "
            "ruda que se limpia la cola con lijas y se va a dormir sin beso "
            "de buenas noches, con la luz apagada."
        ),
        "category": "FRAME",
        "points_cost": 20,
        "image_url": "https://store.gaijin.net/img/content/frame_veteran_pack.png",
        "is_active": True,
    },
    {
        "key": "background_grasoso",
        "name": "fondo grasoso",
        "description": (
            "fondo +1000 de aura papu, para gente alfa lobo solitaria, farmea "
            "una inmensa cantidad de aura en tu perfil con este fondo super "
            "six seven. Al usarlo cualquier minita que vea tu perfil querra "
            "ser tu papunovia, o si, nota del editor: despidanme porfavor."
        ),
        "category": "BACKGROUND",
        "points_cost": 100,
        "image_url": "https://cdn.wallpapersafari.com/19/75/5f8tQY.jpg",
        "is_active": True,
    },
]


def create_demo_cosmetics(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Create initial demo cosmetics for avatar frames and profile backgrounds."""
    del schema_editor
    ExchangeableItem: Any = apps.get_model("actions", "ExchangeableItem")
    for item_data in DEMO_COSMETICS:
        if not ExchangeableItem.objects.filter(key=item_data["key"]).exists():
            ExchangeableItem.objects.create(**item_data)


def eliminate_demo_cosmetics(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Delete the demo cosmetics created when reverting the migration."""
    del schema_editor
    ExchangeableItem: Any = apps.get_model("actions", "ExchangeableItem")
    keys = [item["key"] for item in DEMO_COSMETICS]
    ExchangeableItem.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("actions", "0009_merge_shared_publicly_and_exchangeables"),
    ]

    operations = [
        migrations.RunPython(
            create_demo_cosmetics, reverse_code=eliminate_demo_cosmetics
        ),
    ]
