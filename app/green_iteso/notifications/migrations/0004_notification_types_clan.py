"""Add the clan notification types (T2-45)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_notification_soft_delete"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="notification",
            name="notification_type_valid",
        ),
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("AUDIT_APPROVED", "Photo audit approved"),
                    ("AUDIT_REJECT", "Photo audit rejected"),
                    ("BADGE_EARNED", "Badge earned"),
                    ("CAMPAIGN_INVITE", "Campaign invitation"),
                    ("CLAN_DISSOLVED", "Clan dissolved"),
                    ("CLAN_JOIN_REQUEST", "Clan join request"),
                    ("CLAN_KICKED", "Expelled from a clan"),
                    ("CLAN_LEADER_CHANGED", "Clan leader changed"),
                    ("CLAN_MEMBER_JOINED", "Member joined a clan"),
                    ("CLAN_MEMBER_LEFT", "Member left a clan"),
                    ("CLAN_REQ_ACCEPTED", "Clan join request accepted"),
                    ("CLAN_REQ_REJECTED", "Clan join request rejected"),
                    ("MISSION_COMPLETED", "Mission completed"),
                    ("SOCIAL_FOLLOW", "New follower"),
                    ("SYSTEM", "System announcement"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "notification_type__in",
                        [
                            "AUDIT_APPROVED",
                            "AUDIT_REJECT",
                            "BADGE_EARNED",
                            "CAMPAIGN_INVITE",
                            "CLAN_DISSOLVED",
                            "CLAN_JOIN_REQUEST",
                            "CLAN_KICKED",
                            "CLAN_LEADER_CHANGED",
                            "CLAN_MEMBER_JOINED",
                            "CLAN_MEMBER_LEFT",
                            "CLAN_REQ_ACCEPTED",
                            "CLAN_REQ_REJECTED",
                            "MISSION_COMPLETED",
                            "SOCIAL_FOLLOW",
                            "SYSTEM",
                        ],
                    )
                ),
                name="notification_type_valid",
            ),
        ),
    ]
