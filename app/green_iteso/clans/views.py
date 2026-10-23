"""DRF views for the clans domain."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from green_iteso.accounts.models import Clan, ClanInvitation, ClanMembership, User
from green_iteso.accounts.services import ensure_profile
from green_iteso.core.permissions import IsClanLeader

from .avatars import InvalidClanAvatarError
from .selectors import (
    get_pending_join_requests,
    list_active_clan_invitations,
    list_active_clans,
)
from .serializers import (
    ClanAvatarUploadSerializer,
    ClanDetailSerializer,
    ClanInvitationCreatedSerializer,
    ClanInvitationSerializer,
    ClanMembershipSerializer,
    ClanSerializer,
    InstitutionalAssignmentSerializer,
    InstitutionalOnboardingSerializer,
    JoinRequestDecisionSerializer,
    KickMemberSerializer,
    PendingMembershipRequestSerializer,
    RedeemInvitationSerializer,
    TransferLeadershipSerializer,
)
from .services import (
    DuplicateClanNameError,
    InvalidClanPrivacyError,
    accept_join_request,
    assign_institutional_clan,
    create_clan_invitation,
    create_private_clan,
    dissolve_clan,
    join_clan,
    leave_clan,
    redeem_clan_invitation,
    reject_join_request,
    revoke_clan_invitation,
    set_clan_avatar,
    update_clan,
)
from .services import kick_member as kick_member_service
from .services import select_active_private_clan as select_active_private_clan_service
from .services import transfer_leadership as transfer_leadership_service
from .throttles import ClanInviteRedeemThrottle


def _validation_error_from(
    exc: ValueError, *, field: str | None = None
) -> ValidationError:
    """Convert a domain ValueError into a DRF ValidationError (shared across actions)."""
    if field:
        return ValidationError({field: str(exc)})
    return ValidationError(str(exc))


ACCEPTED_MEMBERSHIPS = Prefetch(
    "memberships",
    queryset=ClanMembership.objects.filter(
        status=ClanMembership.Status.ACCEPTED
    ).select_related("user"),
)
"""Roster prefetch used by ``retrieve``: a PENDING/REJECTED row never counts
as a member, so both ``members`` and ``member_count`` on the detail
serializer stay correct without an extra query."""


def _clan_edit_error(exc: ValueError) -> ValidationError:
    """Attach a clan-edit error to the field the client should highlight."""
    if isinstance(exc, DuplicateClanNameError):
        return _validation_error_from(exc, field="name")
    if isinstance(exc, InvalidClanPrivacyError):
        return _validation_error_from(exc, field="privacy")
    return _validation_error_from(exc)


_clan_edit_schema = extend_schema(
    summary="Editar clan",
    description=(
        "Edita nombre, descripción o privacidad de un clan privado. Solo el "
        "líder del clan. El avatar se cambia con /clans/{id}/avatar/."
    ),
    responses={200: ClanSerializer, 400: None, 403: None, 404: None},
)


@extend_schema_view(update=_clan_edit_schema, partial_update=_clan_edit_schema)
class ClanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create, edit, dissolve, join, leave, and manage clans under /api/v1/clans/."""

    serializer_class = ClanSerializer

    def get_serializer_class(self) -> type[ClanSerializer]:
        if self.action == "retrieve":
            return ClanDetailSerializer
        return ClanSerializer

    def get_queryset(self) -> QuerySet[Clan]:
        # "join" is exempt from the usual visibility filter: a PRIVATE_INVITE
        # clan's applicant is expected to already know the clan (shared out
        # of band, e.g. a link from the leader), so the clan not yet showing
        # up in list_active_clans must not 404 the join attempt.
        if self.action == "join":
            return Clan.objects.all()

        user = self.request.user
        # Global ADMIN can reach and view all clans for administrative overrides
        if getattr(user, "role", None) == User.Role.ADMIN:
            queryset = Clan.objects.all()
        else:
            # `search` only applies to `list`: `get_queryset()` also backs
            # `get_object()` for retrieve and every detail action (destroy,
            # transfer-leadership, select-active, leave, accept/reject-request),
            # where filtering by an unrelated query param would 404 a clan that
            # otherwise exists.
            search = (
                self.request.query_params.get("search", "")
                if self.action == "list"
                else ""
            )
            queryset = list_active_clans(user=user, search=search)

        if self.action == "retrieve":
            return queryset.prefetch_related(ACCEPTED_MEMBERSHIPS)
        return queryset

    def perform_create(self, serializer: ClanSerializer) -> None:
        try:
            serializer.instance = create_private_clan(
                name=serializer.validated_data["name"],
                description=serializer.validated_data.get("description", ""),
                privacy=serializer.validated_data.get("privacy", Clan.Privacy.PUBLIC),
                created_by=self.request.user,
            )
        except ValueError as exc:
            raise _validation_error_from(exc) from exc

    def perform_update(self, serializer: ClanSerializer) -> None:
        """Apply the edit through the leader-only ``update_clan`` service (#164)."""
        data = serializer.validated_data
        try:
            serializer.instance = update_clan(
                clan=serializer.instance,
                actor=self.request.user,
                name=data.get("name"),
                description=data.get("description"),
                privacy=data.get("privacy"),
            )
        except Clan.DoesNotExist as exc:
            raise NotFound() from exc
        except ValueError as exc:
            raise _clan_edit_error(exc) from exc

    def perform_destroy(self, instance: Clan) -> None:
        """Dissolve the clan with a soft delete instead of removing the row (BR-09)."""
        dissolve_clan(clan=instance, actor=self.request.user)

    @extend_schema(
        summary="Listar solicitudes de ingreso pendientes",
        description="Retorna todas las solicitudes PENDING de un clan. Acceso restringido al líder del clan.",
        tags=["Clans Administration"],
        responses={
            200: PendingMembershipRequestSerializer(many=True),
            403: None,
            404: None,
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="requests",
        permission_classes=[IsAuthenticated, IsClanLeader],
    )
    def requests(self, request: Request, **kwargs: object) -> Response:
        """List pending join requests for this clan as its leader (T2-40)."""
        clan = self.get_object()
        pending_qs = get_pending_join_requests(clan_id=clan.id)
        return Response(PendingMembershipRequestSerializer(pending_qs, many=True).data)

    @extend_schema(
        methods=["GET"],
        summary="Listar códigos de invitación activos",
        description=(
            "Códigos sin usar, revocar ni expirar. Nunca incluye el código "
            "completo. Solo el líder del clan."
        ),
        responses={200: ClanInvitationSerializer(many=True), 403: None, 404: None},
    )
    @extend_schema(
        methods=["POST"],
        summary="Generar código de invitación",
        description=(
            "Crea un código de un solo uso que expira en 48 h. El código "
            "completo solo se devuelve aquí. Solo el líder del clan."
        ),
        request=None,
        responses={
            201: ClanInvitationCreatedSerializer,
            400: None,
            403: None,
            404: None,
        },
    )
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="invitations",
        permission_classes=[IsAuthenticated, IsClanLeader],
    )
    def invitations(self, request: Request, **kwargs: object) -> Response:
        """List or create this clan's single-use invite codes as its leader (#165)."""
        clan = self.get_object()
        if request.method == "GET":
            active = list_active_clan_invitations(clan=clan)
            return Response(ClanInvitationSerializer(active, many=True).data)
        try:
            invitation, code = create_clan_invitation(clan=clan, actor=request.user)
        except Clan.DoesNotExist as exc:
            raise NotFound() from exc
        except ValueError as exc:
            raise _validation_error_from(exc) from exc
        payload = {
            "id": invitation.id,
            "code": code,
            "expires_at": invitation.expires_at,
        }
        return Response(
            ClanInvitationCreatedSerializer(payload).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Revocar código de invitación",
        description="Invalida un código de invitación del clan. Solo el líder del clan.",
        responses={204: None, 403: None, 404: None},
    )
    @action(
        detail=True,
        methods=["delete"],
        # A strict UUID, so a malformed id is a 404 from the router instead of
        # a ValidationError from the UUID primary-key lookup.
        url_path=(
            r"invitations/(?P<invitation_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        ),
        permission_classes=[IsAuthenticated, IsClanLeader],
    )
    def revoke_invitation(
        self, request: Request, invitation_id: str, **kwargs: object
    ) -> Response:
        """Revoke one of this clan's invite codes as its leader (#165)."""
        clan = self.get_object()
        try:
            revoke_clan_invitation(
                clan=clan, actor=request.user, invitation_id=invitation_id
            )
        except (
            Clan.DoesNotExist,
            ClanInvitation.DoesNotExist,
            DjangoValidationError,
        ) as exc:
            raise NotFound() from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Unirse a un clan con código de invitación",
        description=(
            "Canjea un código de un solo uso y une al usuario al clan como "
            "miembro aceptado (respeta el límite de 5 clanes privados). "
            "Todos los errores se devuelven en el campo `code`. Limitado a 10 "
            "intentos por minuto por usuario y por proceso del servidor."
        ),
        request=RedeemInvitationSerializer,
        responses={201: ClanMembershipSerializer, 400: None, 429: None},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="redeem-invitation",
        throttle_classes=[ClanInviteRedeemThrottle],
    )
    def redeem_invitation(self, request: Request) -> Response:
        """Join a clan with a single-use invite code (#165)."""
        payload = RedeemInvitationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            membership = redeem_clan_invitation(
                code=payload.validated_data["code"], user=request.user
            )
        except ValueError as exc:
            # One shape for every refusal (unknown code, already a member,
            # BR-04 cap), matching the serializer's own field errors, so
            # clients read ``{"code": [message]}`` in every case.
            raise ValidationError({"code": [str(exc)]}) from exc
        return Response(
            ClanMembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request: Request, **kwargs: object) -> Response:
        """Join a PUBLIC clan directly, or file a pending request for a
        PRIVATE_INVITE one (T2-32).
        """
        clan = self.get_object()
        try:
            membership = join_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise _validation_error_from(exc, field="clan") from exc
        return Response(
            ClanMembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request: Request, **kwargs: object) -> Response:
        """Leave a private clan the caller currently belongs to (T2-33)."""
        clan = self.get_object()
        try:
            leave_clan(clan=clan, user=request.user)
        except ValueError as exc:
            raise _validation_error_from(exc, field="clan") from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="accept-request")
    def accept_request(self, request: Request, **kwargs: object) -> Response:
        """Accept a pending join request as the clan's leader (T2-32)."""
        clan = self.get_object()
        payload = JoinRequestDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            applicant = User.objects.get(pk=payload.validated_data["applicant_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"applicant_id": "User not found."}) from exc
        try:
            membership = accept_join_request(
                clan=clan, actor=request.user, applicant=applicant
            )
        except ValueError as exc:
            raise _validation_error_from(exc, field="applicant_id") from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="reject-request")
    def reject_request(self, request: Request, **kwargs: object) -> Response:
        """Reject a pending join request as the clan's leader (T2-32)."""
        clan = self.get_object()
        payload = JoinRequestDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            applicant = User.objects.get(pk=payload.validated_data["applicant_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"applicant_id": "User not found."}) from exc
        try:
            membership = reject_join_request(
                clan=clan, actor=request.user, applicant=applicant
            )
        except ValueError as exc:
            raise _validation_error_from(exc, field="applicant_id") from exc
        return Response(ClanMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="transfer-leadership")
    def transfer_leadership(self, request: Request, **kwargs: object) -> Response:
        """Transfer this clan's leadership to another of its members (T2-42).

        Takes ``**kwargs`` rather than a named ``pk`` because the value is
        never read directly here: ``self.get_object()`` already resolves it
        from ``self.kwargs`` (set by ``dispatch()``), applying the view's
        queryset and permissions in the process.
        """
        clan = self.get_object()
        payload = TransferLeadershipSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            successor = User.objects.get(pk=payload.validated_data["successor_id"])
        except User.DoesNotExist as exc:
            raise ValidationError({"successor_id": "User not found."}) from exc
        try:
            membership = transfer_leadership_service(
                clan=clan, actor=request.user, successor=successor
            )
        except ValueError as exc:
            raise _validation_error_from(exc, field="successor_id") from exc
        return Response(ClanMembershipSerializer(membership).data)

    @extend_schema(
        methods=["POST"],
        summary="Subir avatar del clan",
        description="Reemplaza el avatar con una imagen JPG, PNG o WEBP de hasta 5 MB. Solo el líder del clan.",
        request={"multipart/form-data": ClanAvatarUploadSerializer},
        responses={200: ClanSerializer, 400: None, 403: None, 404: None},
    )
    @extend_schema(
        methods=["DELETE"],
        summary="Quitar avatar del clan",
        description="Quita el avatar del clan. Solo el líder del clan.",
        request=None,
        responses={200: ClanSerializer, 403: None, 404: None},
    )
    @action(
        detail=True,
        methods=["post", "delete"],
        url_path="avatar",
        parser_classes=[MultiPartParser],
    )
    def avatar(self, request: Request, **kwargs: object) -> Response:
        """Upload or remove this clan's avatar as its leader (#164)."""
        clan = self.get_object()
        uploaded = None
        if request.method == "POST":
            payload = ClanAvatarUploadSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            uploaded = payload.validated_data["avatar"]
        try:
            updated = set_clan_avatar(clan=clan, actor=request.user, uploaded=uploaded)
        except Clan.DoesNotExist as exc:
            raise NotFound() from exc
        except InvalidClanAvatarError as exc:
            raise _validation_error_from(exc, field="avatar") from exc
        return Response(ClanSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="select-active")
    def select_active(self, request: Request, **kwargs: object) -> Response:
        """Set this clan as the caller's active private clan (T2-35).

        Takes ``**kwargs`` rather than a named ``pk`` because the value is
        never read directly here: ``self.get_object()`` already resolves it
        from ``self.kwargs`` (set by ``dispatch()``), applying the view's
        queryset and permissions in the process.
        """
        clan = self.get_object()
        try:
            membership = select_active_private_clan_service(
                user=request.user, clan=clan
            )
        except ValueError as exc:
            raise _validation_error_from(exc, field="clan") from exc
        return Response(ClanMembershipSerializer(membership).data)

    @extend_schema(
        summary="Expulsar a un miembro del clan",
        description="Permite únicamente al líder del clan expulsar a un miembro activo.",
        tags=["Clans Administration"],
        request=KickMemberSerializer,
        responses={204: None, 400: None, 403: None, 404: None},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="kick",
        permission_classes=[IsAuthenticated, IsClanLeader],
    )
    def kick_member(self, request: Request, **kwargs: object) -> Response:
        """Expel a member from the clan as its leader (T2-41)."""
        clan = self.get_object()
        serializer = KickMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user_id = serializer.validated_data["user_id"]

        try:
            target_user = User.objects.get(pk=target_user_id)
        except User.DoesNotExist as exc:
            raise ValidationError({"user_id": "User not found."}) from exc

        try:
            kick_member_service(clan=clan, actor=request.user, target_user=target_user)
        except Clan.DoesNotExist as exc:
            raise NotFound() from exc
        except ValueError as exc:
            raise _validation_error_from(exc, field="user_id") from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


class InstitutionalClanAssignmentView(APIView):
    """Read or declare the caller's institutional clan assignment (T2-30)."""

    def get(self, request: Request) -> Response:
        """Return the caller's current onboarding/institutional clan state."""
        profile = ensure_profile(request.user)
        return Response(InstitutionalAssignmentSerializer(profile).data)

    def post(self, request: Request) -> Response:
        """Declare a career and auto-assign its institutional clan."""
        payload = InstitutionalOnboardingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            profile = assign_institutional_clan(
                user=request.user, career=payload.validated_data["career"]
            )
        except ValueError as exc:
            raise _validation_error_from(exc, field="career") from exc
        return Response(
            InstitutionalAssignmentSerializer(profile).data,
            status=status.HTTP_200_OK,
        )
