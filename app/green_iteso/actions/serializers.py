from rest_framework import serializers


class ActionLogSerializer(serializers.Serializer):
    action_id = serializers.UUIDField()
    idempotency_key = serializers.CharField(max_length=128)
    evidence_object_key = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
