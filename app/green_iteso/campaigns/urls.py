"""URL routes for the campaigns API."""

from django.urls import path

from .views import (
    CampaignDetailView,
    CampaignJoinView,
    CampaignListCreateView,
    CampaignParticipantListView,
    MissionProgressView,
)

urlpatterns = [
    path("campaigns/", CampaignListCreateView.as_view(), name="campaign-list"),
    path(
        "campaigns/<uuid:campaign_id>/",
        CampaignDetailView.as_view(),
        name="campaign-detail",
    ),
    path(
        "campaigns/<uuid:campaign_id>/participants/",
        CampaignParticipantListView.as_view(),
        name="campaign-participants",
    ),
    path(
        "campaigns/<uuid:campaign_id>/join/",
        CampaignJoinView.as_view(),
        name="campaign-join",
    ),
    path(
        "missions/<uuid:mission_id>/progress/",
        MissionProgressView.as_view(),
        name="mission-progress",
    ),
]
