from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0002_campaign_campaign_scope_valid_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mission",
            name="action",
            field=models.ForeignKey(
                db_column="action_master_id",
                on_delete=models.PROTECT,
                related_name="missions",
                to="actions.actionmaster",
            ),
        ),
    ]
