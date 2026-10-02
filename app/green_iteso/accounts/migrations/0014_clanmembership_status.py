"""Add ClanMembership.status to support join requests (T2-32/T2-33)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_merge_friendship_and_role_audit_ranking"),
    ]

    operations = [
        migrations.AddField(
            model_name="clanmembership",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("ACCEPTED", "Accepted"),
                    ("REJECTED", "Rejected"),
                ],
                default="ACCEPTED",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="clanmembership",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["PENDING", "ACCEPTED", "REJECTED"])),
                name="membership_status_valid",
            ),
        ),
    ]
