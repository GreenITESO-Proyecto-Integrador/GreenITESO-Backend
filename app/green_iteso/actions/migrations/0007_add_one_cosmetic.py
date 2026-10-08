from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import migrations

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def create_cosmetic(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Create a single 'OTHER' type cosmetic if it does not already exist.

    - key: ``other_special`` (unique identifier, used by the frontend).
    - category: ``OTHER`` – displayed as a generic option in the store.
    - image_url: the URL provided by the user.
    - points_cost: 10 (the positive minimum required by the ``exchangeable_points_cost_positive`` constraint).
    """
    del schema_editor
    ExchangeableItem: Any = apps.get_model('actions', 'ExchangeableItem')
    # Evitamos duplicados verificando la existencia del ``key``.
    if not ExchangeableItem.objects.filter(key='1').exists():
        ExchangeableItem.objects.create(
            key='1',
            name='Fondo Ecologico',
            description='Cosmético de fondo verde.',
            category='BACKGROUND',
            points_cost=10,
            image_url='https://wallpaperaccess.com/full/9378120.jpg',
            is_active=True,
        )


def eliminate_cosmetic(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Delete the cosmetic created.

    Executed when reverting the migration.
    """
    del schema_editor
    ExchangeableItem: Any = apps.get_model('actions', 'ExchangeableItem')
    ExchangeableItem.objects.filter(key='other_special').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('actions', '0006_reward_rewardredemption'),
    ]

    operations = [
        migrations.RunPython(create_cosmetic, reverse_code=eliminate_cosmetic),
    ]
