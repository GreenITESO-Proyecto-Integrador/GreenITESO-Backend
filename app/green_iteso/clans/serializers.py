"""DRF serializers for the clans domain."""

from __future__ import annotations

from rest_framework import serializers

from green_iteso.accounts.models import Clan


class ClanSerializer(serializers.ModelSerializer):
    """Representation of a clan; creation delegates to clans.services.create_clan."""

    class Meta:
        model = Clan
        fields = [
            "id",
            "name",
            "description",
            "type",
            "privacy",
            "total_points",
            "created_at",
        ]
        read_only_fields = ["id", "privacy", "total_points", "created_at"]
