"""Table for the shared auth throttle cache (#217).

Same layout ``createcachetable`` builds for ``DatabaseCache``, declared here so
the schema keeps coming only from migrations. The name matches
``CACHES["throttle"]["LOCATION"]``.
"""

from django.db import migrations

TABLE = "accounts_auth_throttle_cache"


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_user_ban_audit"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                f"""
                CREATE TABLE {TABLE} (
                    cache_key varchar(255) NOT NULL PRIMARY KEY,
                    value text NOT NULL,
                    expires timestamp with time zone NOT NULL
                )
                """,
                f"CREATE INDEX {TABLE}_expires ON {TABLE} (expires)",
            ],
            reverse_sql=[f"DROP TABLE {TABLE}"],
        ),
    ]
