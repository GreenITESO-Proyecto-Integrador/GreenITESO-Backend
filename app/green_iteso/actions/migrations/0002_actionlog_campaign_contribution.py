import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("actions", "0001_initial"),
        ("campaigns", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="actionlog",
            name="campaign",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="action_logs",
                to="campaigns.campaign",
            ),
        ),
        migrations.CreateModel(
            name="ActionLogMissionContribution",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "action_log",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mission_contributions",
                        to="actions.actionlog",
                    ),
                ),
                (
                    "mission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="action_contributions",
                        to="campaigns.mission",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("action_log", "mission"),
                        name="action_log_mission_unique",
                    )
                ]
            },
        ),
    ]
