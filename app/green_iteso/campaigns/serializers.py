"""Django REST Framework serializers for campaigns and mission progress."""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from green_iteso.accounts.models import Clan
from green_iteso.actions.models import ActionMaster
from green_iteso.core.roles import GlobalRole

from .models import Campaign, CampaignParticipant, Mission, UserMissionProgress
from .services import (
    can_create_campaign,
    can_manage_campaign_for_user,
    compute_campaign_status,
    create_campaign_with_missions,
)


class ActionMasterSerializer(serializers.ModelSerializer):
    """Expose the action catalog fields needed by a mission."""

    class Meta:
        model = ActionMaster
        fields = ("code", "name", "description", "points")
        ref_name = "CampaignActionMaster"


class MissionSerializer(serializers.ModelSerializer):
    """Serialize a campaign mission and its read-only action details."""

    action = ActionMasterSerializer(read_only=True)
    action_id = serializers.PrimaryKeyRelatedField(
        source="action",
        queryset=ActionMaster.objects.filter(is_active=True),
        write_only=True,
        error_messages={"does_not_exist": "Action does not exist or is inactive."},
    )
    target_count = serializers.IntegerField(
        min_value=1,
        error_messages={"min_value": "Target count must be greater than zero."},
    )

    class Meta:
        model = Mission
        fields = ("id", "campaign", "action", "action_id", "target_count")
        extra_kwargs = {
            "id": {"read_only": True},
            "campaign": {"read_only": True},
        }

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Reject an action that already has a mission in the context campaign."""
        campaign = self.context.get("campaign")
        if (
            campaign is not None
            and Mission.objects.filter(
                campaign=campaign, action=attrs.get("action")
            ).exists()
        ):
            raise serializers.ValidationError(
                {"action_id": "This action already has a mission in this campaign."}
            )
        return attrs


class CampaignSerializer(serializers.ModelSerializer):
    """Serialize campaigns, including missions during campaign creation."""

    missions = MissionSerializer(many=True, required=False)
    target_clan = serializers.PrimaryKeyRelatedField(
        queryset=Clan.objects.all(),
        required=False,
        allow_null=True,
        error_messages={"does_not_exist": "Clan does not exist or was deleted."},
    )
    is_participant = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

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
            "approval_status",
            "is_participant",
            "can_manage",
        )
        extra_kwargs = {
            "id": {"read_only": True},
            "created_at": {"read_only": True},
            "creator": {"read_only": True},
            "status": {"read_only": True},
            "approval_status": {"read_only": True},
        }

    def validate_missions(self, missions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reject nested missions that repeat an action."""
        action_ids = [mission["action"].pk for mission in missions]
        if len(action_ids) != len(set(action_ids)):
            raise serializers.ValidationError(
                "Each action can appear only once per campaign."
            )
        return missions

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Validate campaign dates and scope-specific clan requirements."""
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start_date is not None and end_date is not None and start_date >= end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )
        if self.instance is None and end_date is not None:
            if end_date <= timezone.now():
                raise serializers.ValidationError(
                    {"end_date": "End date must be in the future."}
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

        if scope == Campaign.Scope.PRIVATE and target_clan.type not in (
            Clan.ClanType.PRIVATE,
            Clan.ClanType.INSTITUTIONAL,
        ):
            raise serializers.ValidationError(
                {
                    "target_clan": (
                        "Private campaigns must target a private or "
                        "institutional clan."
                    )
                }
            )

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if (
            self.instance is None
            and user is not None
            and scope == Campaign.Scope.PRIVATE
            and target_clan.type == Clan.ClanType.INSTITUTIONAL
            and user.role != GlobalRole.ADMIN
        ):
            raise serializers.ValidationError(
                {
                    "target_clan": (
                        "Only administrators can create campaigns for an "
                        "institutional clan."
                    )
                }
            )
        if (
            self.instance is None
            and user is not None
            and not can_create_campaign(user, scope, target_clan)
        ):
            if scope == Campaign.Scope.GLOBAL:
                raise PermissionDenied(
                    "Only administrators can create global campaigns."
                )
            raise PermissionDenied(
                "Only the clan leader can create campaigns for this clan."
            )
        return attrs

    def get_is_participant(self, instance: Campaign) -> bool:
        """Return whether the request user is enrolled, using the annotation if present."""
        annotated = getattr(instance, "is_participant", None)
        if annotated is not None:
            return bool(annotated)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return instance.participants.filter(user=user).exists()

    def get_can_manage(self, instance: Campaign) -> bool:
        """Return whether the request user may manage this campaign.

        Uses ``context["leader_clan_ids"]`` when the view precomputed it, to
        avoid a per-campaign leadership query.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        leader_clan_ids = self.context.get("leader_clan_ids")
        return can_manage_campaign_for_user(user, instance, leader_clan_ids)

    def create(self, validated_data: dict[str, Any]) -> Campaign:
        """Create the campaign and its nested missions atomically."""
        missions_data = validated_data.pop("missions", [])
        validated_data["status"] = compute_campaign_status(
            validated_data["start_date"], validated_data["end_date"], timezone.now()
        )
        return create_campaign_with_missions(validated_data, missions_data)


class CampaignUpdateSerializer(serializers.ModelSerializer):
    """Validate edits to a promotion campaign's title, description and dates."""

    class Meta:
        model = Campaign
        fields = ("title", "description", "start_date", "end_date")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Reject non-editable fields and inconsistent or past-ending dates."""
        unknown = sorted(set(self.initial_data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {field: "This field cannot be edited." for field in unknown}
            )
        start_date = attrs.get("start_date", self.instance.start_date)
        end_date = attrs.get("end_date", self.instance.end_date)
        if start_date >= end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )
        if end_date <= timezone.now():
            raise serializers.ValidationError(
                {"end_date": "End date must be in the future."}
            )
        return attrs

    def update(self, instance: Campaign, validated_data: dict[str, Any]) -> Campaign:
        """Save the edits and recompute the lifecycle status from the dates."""
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.status = compute_campaign_status(
            instance.start_date, instance.end_date, timezone.now()
        )
        instance.save(update_fields=[*validated_data, "status"])
        return instance


class CampaignProposalSerializer(serializers.ModelSerializer):
    """Serialize a user's proposal for a global campaign pending admin review."""

    missions = MissionSerializer(many=True, required=False)

    class Meta:
        model = Campaign
        fields = (
            "id",
            "title",
            "description",
            "scope",
            "status",
            "approval_status",
            "creator",
            "start_date",
            "end_date",
            "created_at",
            "missions",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
        )
        extra_kwargs = {
            "id": {"read_only": True},
            "created_at": {"read_only": True},
            "creator": {"read_only": True},
            "scope": {"read_only": True},
            "status": {"read_only": True},
            "approval_status": {"read_only": True},
            "reviewed_by": {"read_only": True},
            "reviewed_at": {"read_only": True},
            "rejection_reason": {"read_only": True},
        }

    def validate_missions(self, missions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reject nested missions that repeat an action."""
        action_ids = [mission["action"].pk for mission in missions]
        if len(action_ids) != len(set(action_ids)):
            raise serializers.ValidationError(
                "Each action can appear only once per campaign."
            )
        return missions

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Validate the proposed campaign's dates."""
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date is not None and end_date is not None and start_date >= end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )
        if end_date is not None and end_date <= timezone.now():
            raise serializers.ValidationError(
                {"end_date": "End date must be in the future."}
            )
        return attrs


class CampaignRejectSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the reason required to reject a campaign proposal."""

    rejection_reason = serializers.CharField(allow_blank=False)


class CampaignParticipantSerializer(serializers.ModelSerializer):
    """Serialize a user's participation in a campaign."""

    class Meta:
        model = CampaignParticipant
        fields = ("campaign", "user", "joined_at")
        extra_kwargs = {"joined_at": {"read_only": True}}


class UserMissionProgressSerializer(serializers.ModelSerializer):
    """Serialize a user's progress toward a mission."""

    mission = serializers.PrimaryKeyRelatedField(read_only=True)
    target_count = serializers.IntegerField(
        source="mission.target_count", read_only=True
    )
    progress_percentage = serializers.SerializerMethodField()

    class Meta:
        model = UserMissionProgress
        fields = (
            "mission",
            "current_count",
            "is_completed",
            "target_count",
            "progress_percentage",
        )
        extra_kwargs = {
            "current_count": {"read_only": True},
            "is_completed": {"read_only": True},
        }

    def get_progress_percentage(self, instance: UserMissionProgress) -> float:
        """Return current progress as a percentage of the mission target."""
        target_count = instance.mission.target_count
        if target_count == 0:
            return 0.0
        return instance.current_count / target_count * 100


class CampaignListItemSerializer(CampaignSerializer):
    """Document the ``user_mission_progress`` field added by hand in list responses."""

    user_mission_progress = UserMissionProgressSerializer(many=True, read_only=True)

    class Meta(CampaignSerializer.Meta):
        fields = (*CampaignSerializer.Meta.fields, "user_mission_progress")


class CampaignDetailSerializer(CampaignListItemSerializer):
    """Document the ``participants`` field added by hand in detail responses."""

    participants = CampaignParticipantSerializer(many=True, read_only=True)

    class Meta(CampaignListItemSerializer.Meta):
        fields = (*CampaignListItemSerializer.Meta.fields, "participants")
