from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("actions", "0005_alter_actioncategory_table_alter_actionlog_table_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="actionlog",
            name="is_shared_publicly",
            field=models.BooleanField(default=False),
        ),
    ]
