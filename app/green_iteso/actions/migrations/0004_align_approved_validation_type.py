from typing import Any

from django.db import migrations, models


def rename_legacy_validation_value(apps: Any, schema_editor: Any) -> None:
    """Translate the old declarative-button value before renaming the field."""
    action_master = apps.get_model("actions", "ActionMaster")
    action_master.objects.using(schema_editor.connection.alias).filter(
        validation_mode="DECLARATIVE_BUTTON"
    ).update(validation_mode="NONE")


def restore_legacy_validation_value(apps: Any, schema_editor: Any) -> None:
    """Restore the previous value when this alignment migration is reversed."""
    action_master = apps.get_model("actions", "ActionMaster")
    action_master.objects.using(schema_editor.connection.alias).filter(
        validation_mode="NONE"
    ).update(validation_mode="DECLARATIVE_BUTTON")


class Migration(migrations.Migration):
    dependencies = [
        ("actions", "0003_actionlog_action_log_status_valid_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="actionmaster",
            name="action_validation_mode_valid",
        ),
        migrations.RunPython(
            rename_legacy_validation_value,
            restore_legacy_validation_value,
        ),
        migrations.RenameField(
            model_name="actionmaster",
            old_name="validation_mode",
            new_name="validation_type",
        ),
        migrations.AlterField(
            model_name="actionmaster",
            name="validation_type",
            field=models.CharField(
                choices=[("NONE", "None"), ("PHOTO", "Photo")],
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="actionmaster",
            constraint=models.CheckConstraint(
                condition=models.Q(("validation_type__in", ["NONE", "PHOTO"])),
                name="action_validation_type_valid",
            ),
        ),
    ]
