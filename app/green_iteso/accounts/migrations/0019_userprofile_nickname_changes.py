"""Count nickname changes so only the first one is free (#175)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_userprofile_avatar_object_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="nickname_changes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
