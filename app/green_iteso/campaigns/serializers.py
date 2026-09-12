"""Django REST Framework serializers for campaigns and mission progress."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from green_iteso.actions.models import ActionMaster

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress


class ActionMasterSerializer(serializers.ModelSerializer):
    """Expose the action catalog fields needed by a mission."""

    class Meta:
        model = ActionMaster
        fields = ("code", "name", "description", "points")


class MissionSerializer(serializers.ModelSerializer):
    """Serialize a campaign mission and its read-only action details."""

    action = ActionMasterSerializer(read_only=True)
    action_id = serializers.PrimaryKeyRelatedField(
        source="action",
        queryset=ActionMaster.objects.all(),
        write_only=True,
    )
    target_count = serializers.IntegerField(
        min_value=1,
        error_messages={"min_value": "Target count must be greater than zero."},
    )

    class Meta:
        model = Mission
        fields = ("id", "campaign", "action", "action_id", "target_count")
        extra_kwargs = {"id": {"read_only": True}, "campaign": {"required": False}}


class CampaignSerializer(serializers.ModelSerializer):
    """Serialize campaigns, including missions during campaign creation."""

    missions = MissionSerializer(many=True, required=False)

    class Meta:
        model = Campaign
        fields = (
            "id",
            "title",
            "description",
            "scope",
            "status",
            "creator",
            "target_clan",
            "start_date",
            "end_date",
            "created_at",
            "missions",
        )
        extra_kwargs = {
            "id": {"read_only": True},
            "created_at": {"read_only": True},
            "creator": {"read_only": True},
        }

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Validate campaign dates and scope-specific clan requirements."""
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start_date is not None and end_date is not None and start_date >= end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )

        scope = attrs.get("scope", getattr(self.instance, "scope", None))
        target_clan = attrs.get(
            "target_clan", getattr(self.instance, "target_clan", None)
        )
        if scope == Campaign.Scope.GLOBAL and target_clan is not None:
            raise serializers.ValidationError(
                {"target_clan": "Global campaigns cannot target a clan."}
            )
        if scope == Campaign.Scope.PRIVATE and target_clan is None:
            raise serializers.ValidationError(
                {"target_clan": "Private campaigns must target a clan."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Campaign:
        """Create the campaign and its nested missions atomically."""
        missions_data = validated_data.pop("missions", [])
        campaign = Campaign.objects.create(**validated_data)
        for mission_data in missions_data:
            Mission.objects.create(campaign=campaign, **mission_data)
        return campaign


class CampaignParticipantSerializer(serializers.ModelSerializer):
    """Serialize a user's participation in a campaign."""

    class Meta:
        model = CampaignParticipant
        fields = ("campaign", "user", "joined_at")
        extra_kwargs = {"joined_at": {"read_only": True}}


class UserMissionProgressSerializer(serializers.ModelSerializer):
    """Serialize a user's progress toward a mission."""

    target_count = serializers.IntegerField(
        source="mission.target_count", read_only=True
    )
    progress_percentage = serializers.SerializerMethodField()

    class Meta:
        model = UserMissionProgress
        fields = (
            "current_count",
            "is_completed",
            "target_count",
            "progress_percentage",
        )
        extra_kwargs = {"is_completed": {"read_only": True}}

    def get_progress_percentage(self, instance: UserMissionProgress) -> float:
        """Return current progress as a percentage of the mission target."""
        target_count = instance.mission.target_count
        if target_count == 0:
            return 0.0
        return instance.current_count / target_count * 100
