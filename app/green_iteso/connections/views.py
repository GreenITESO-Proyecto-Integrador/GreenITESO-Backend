"""DRF views for the connections (friendships) domain."""

from __future__ import annotations

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from green_iteso.accounts.models import Friendship

from .selectors import list_own_friendships
from .serializers import FriendshipSerializer, SendFriendRequestSerializer
from .services import (
    AlreadyFriendsError,
    CannotFriendSelfError,
    FriendRequestAlreadyPendingError,
    FriendRequestNotFoundError,
    FriendRequestNotPendingError,
    respond_to_friend_request,
    send_friend_request,
)


class FriendshipViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """Send, list, accept, and reject friend requests under /api/v1/friendships/."""

    serializer_class = FriendshipSerializer

    def get_queryset(self) -> QuerySet[Friendship]:
        if (
            getattr(self, "swagger_fake_view", False)
            or not self.request.user.is_authenticated
        ):
            return Friendship.objects.none()
        queryset = list_own_friendships(self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())
        return queryset

    @extend_schema(
        request=SendFriendRequestSerializer,
        responses={
            201: FriendshipSerializer,
            400: OpenApiResponse(
                description="Self-request, already pending, or already friends"
            ),
        },
    )
    def create(self, request: Request, *args: object, **kwargs: object) -> Response:
        """Send a friend request to another user."""
        payload = SendFriendRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            friendship = send_friend_request(
                requester=request.user, addressee=payload.validated_data["addressee"]
            )
        except CannotFriendSelfError as exc:
            raise ValidationError({"addressee": str(exc)}) from exc
        except FriendRequestAlreadyPendingError as exc:
            raise ValidationError({"addressee": str(exc)}) from exc
        except AlreadyFriendsError as exc:
            raise ValidationError({"addressee": str(exc)}) from exc
        return Response(
            FriendshipSerializer(friendship).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=None,
        responses={
            200: FriendshipSerializer,
            400: OpenApiResponse(description="Request already responded to"),
            404: OpenApiResponse(description="No such pending request"),
        },
    )
    @action(detail=True, methods=["post"])
    def accept(self, request: Request, pk: str | None = None) -> Response:
        """Accept a pending friend request sent to the caller."""
        return self._respond(pk, accept=True)

    @extend_schema(
        request=None,
        responses={
            200: FriendshipSerializer,
            400: OpenApiResponse(description="Request already responded to"),
            404: OpenApiResponse(description="No such pending request"),
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: str | None = None) -> Response:
        """Reject a pending friend request sent to the caller."""
        return self._respond(pk, accept=False)

    def _respond(self, friendship_id: str | None, *, accept: bool) -> Response:
        try:
            friendship = respond_to_friend_request(
                friendship_id=friendship_id,
                responder=self.request.user,
                accept=accept,
            )
        except FriendRequestNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except FriendRequestNotPendingError as exc:
            raise ValidationError({"status": str(exc)}) from exc
        return Response(FriendshipSerializer(friendship).data)
