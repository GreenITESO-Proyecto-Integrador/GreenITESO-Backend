"""DRF serializers for the actions domain."""

from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from rest_framework import serializers

from green_iteso.accounts.models import User

from .evidence import evidence_is_readable, validate_evidence_upload
from .models import ActionCategory, ActionLog, ActionMaster, ExchangeableItem


class ActionCategorySerializer(serializers.ModelSerializer):
    """Representation of an action category."""

    class Meta:
        model = ActionCategory
        fields = ["id", "code", "name", "description", "icon"]
        read_only_fields = ["id", "code", "name", "description", "icon"]


class ActionMasterSerializer(serializers.ModelSerializer):
    """Representation of an action master definition."""

    category = ActionCategorySerializer(read_only=True)

    class Meta:
        model = ActionMaster
        fields = [
            "id",
            "code",
            "category",
            "name",
            "description",
            "points",
            "daily_limit",
            "validation_type",
            "co2_kg_factor",
            "water_liters_factor",
            "plastic_kg_factor",
            "is_active",
        ]
        read_only_fields = fields


class ActionLogSerializer(serializers.Serializer):
    """Serializer to validate incoming data for action logging.

    The registration form sends the photograph as the multipart field
    ``evidence``. ``evidence_object_key`` remains for clients that already
    stored the object elsewhere.
    """

    action_id = serializers.UUIDField()
    idempotency_key = serializers.CharField(max_length=128)
    evidence = serializers.FileField(required=False, allow_empty_file=False)
    evidence_object_key = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    is_shared_publicly = serializers.BooleanField(required=False, default=False)

    def validate_evidence(self, uploaded: UploadedFile) -> UploadedFile:
        validate_evidence_upload(uploaded)
        return uploaded

    def create(self, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Stub required by abstract base class definition."""
        raise NotImplementedError


class ActionLogListQuerySerializer(serializers.Serializer):
    """Validate the audit-queue filter. Missing status means the pending queue."""

    status = serializers.ChoiceField(
        choices=ActionLog.Status.choices,
        default=ActionLog.Status.PENDING_AUDIT,
        required=False,
        error_messages={"invalid_choice": "Estado de auditoría no válido."},
    )

    def create(self, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError


class ActionLogQueueUserSerializer(serializers.ModelSerializer):
    """Identity an auditor needs to recognize who submitted the log."""

    class Meta:
        model = User
        fields = ["id", "email", "nickname"]
        read_only_fields = fields


class ActionLogQueueActionSerializer(serializers.ModelSerializer):
    """Action fields an auditor needs before opening the evidence."""

    class Meta:
        model = ActionMaster
        fields = ["id", "code", "name", "validation_type"]
        read_only_fields = fields


class ActionLogQueueSerializer(serializers.ModelSerializer):
    """One row of the admin audit queue."""

    user = ActionLogQueueUserSerializer(read_only=True)
    action = ActionLogQueueActionSerializer(read_only=True)
    evidence_url = serializers.SerializerMethodField()

    class Meta:
        model = ActionLog
        fields = [
            "id",
            "status",
            "created_at",
            "points_awarded",
            "evidence_object_key",
            "evidence_url",
            "user",
            "action",
        ]
        read_only_fields = fields

    def get_evidence_url(self, action_log: ActionLog) -> str:
        """Return the admin-only URL when this log has a stored image."""
        request = self.context.get("request")
        if request is None or not evidence_is_readable(action_log.evidence_object_key):
            return ""
        return request.build_absolute_uri(
            reverse("action-log-evidence", kwargs={"log_id": action_log.id})
        )


class ActionLogAuditSerializer(serializers.Serializer):
    """Serializer to validate action log audit data from admins."""

    status = serializers.ChoiceField(choices=["APPROVED", "REJECTED"])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("status") == "REJECTED" and not attrs.get("rejection_reason"):
            raise serializers.ValidationError(
                {"rejection_reason": "Se requiere un motivo al rechazar la evidencia."}
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError


class ExchangeableItemSerializer(serializers.ModelSerializer):
    """Representation of a virtual exchangeable item in the shop."""

    class Meta:
        model = ExchangeableItem
        fields = [
            "id",
            "key",
            "name",
            "description",
            "category",
            "points_cost",
            "is_active",
            "image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RedeemExchangeableRequestSerializer(serializers.Serializer):
    """Validation serializer when redeeming a virtual item via JSON body."""

    item_id = serializers.UUIDField(required=False)
    item_key = serializers.CharField(max_length=50, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("item_id") and not attrs.get("item_key"):
            raise serializers.ValidationError(
                "Debes enviar 'item_key' o 'item_id' en el cuerpo de la petición."
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise NotImplementedError
