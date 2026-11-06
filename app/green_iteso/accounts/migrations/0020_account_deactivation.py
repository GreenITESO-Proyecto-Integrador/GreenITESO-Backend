"""Support deactivating an account and starting over on reactivation (#176)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_userprofile_nickname_changes"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="progress_reset_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
