"""URL routes for the campaigns API."""

from django.urls import path

from .views import (
    CampaignDetailView,
    CampaignJoinView,
    CampaignListCreateView,
    CampaignMissionListCreateView,
    CampaignParticipantListView,
    CampaignProposalApproveView,
    CampaignProposalListCreateView,
    CampaignProposalRejectView,
    MissionProgressView,
)

urlpatterns = [
    path("campaigns/", CampaignListCreateView.as_view(), name="campaign-list"),
    path(
        "campaigns/proposals/",
        CampaignProposalListCreateView.as_view(),
        name="campaign-proposals",
    ),
    path(
        "campaigns/proposals/<uuid:campaign_id>/approve/",
        CampaignProposalApproveView.as_view(),
        name="campaign-proposal-approve",
    ),
    path(
        "campaigns/proposals/<uuid:campaign_id>/reject/",
        CampaignProposalRejectView.as_view(),
        name="campaign-proposal-reject",
    ),
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
        "campaigns/<uuid:campaign_id>/missions/",
        CampaignMissionListCreateView.as_view(),
        name="campaign-missions",
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
