from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_userprofile_public_ranking_index"),
        ("accounts", "0011_merge_profile_editing_and_role_audit"),
    ]

    operations = []
