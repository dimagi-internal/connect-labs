"""DRF views serving synthetic opportunity data in real-Connect export shape.

Scope: ``labs_only=True`` synthetic opps (IDs >= 10_000), gated by
``SyntheticOpportunity.is_visible_to``. All data is read through the existing
``SyntheticExportClient`` / ``FixtureStore`` — these views only authenticate,
authorize, paginate, and shape the response.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from connect_labs.labs.integrations.connect.factory import get_export_client
from connect_labs.labs.synthetic.client import SyntheticExportClient
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.org_tree import synthetic_org_slug, synthetic_program_id

from .authentication import MCPTokenAuthentication
from .pagination import IdKeysetPagination
from .serializers import ExportPageSerializer

# app_structure parity with real Connect (data_export/const.py).
_APP_TYPE_LEARN = "learn"
_APP_TYPE_DELIVER = "deliver"
_APP_TYPE_BOTH = "both"
_VALID_APP_TYPES = (_APP_TYPE_LEARN, _APP_TYPE_DELIVER, _APP_TYPE_BOTH)

_PAGE_PARAMS = [
    OpenApiParameter(
        "last_id", OpenApiTypes.INT, description="Keyset cursor: id of the last row on the previous page."
    ),
    OpenApiParameter("page_size", OpenApiTypes.INT, description="Rows per page (default 1000, max 5000)."),
    OpenApiParameter(
        "cursor_order",
        OpenApiTypes.STR,
        enum=["forward", "reverse"],
        description="Cursor direction (default forward).",
    ),
]


def _visible_opp_or_404(user, opportunity_id):
    """Return the opp if it's a labs-only opp visible to ``user``, else 404.

    Uses a 404 (not 403) for not-visible/not-registered alike, mirroring real
    Connect's 404-on-no-access and avoiding leaking which opp IDs exist.
    """
    opp = SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id).first()
    if opp is None or not opp.is_visible_to(user):
        raise NotFound("Opportunity not found.")
    return opp


def _synthetic_client(opportunity_id):
    """Build the fixture-backed client for a registered, enabled synthetic opp."""
    client = get_export_client(opportunity_id, access_token="")
    if not isinstance(client, SyntheticExportClient):
        # A visible opp is always registered + enabled, so this is defensive
        # only — never serve real Connect data through this surface.
        raise NotFound("Opportunity not found.")
    return client


class _ExportView(APIView):
    authentication_classes = [MCPTokenAuthentication]
    permission_classes = [IsAuthenticated]


class OpportunityListView(_ExportView):
    """GET /api/export/opportunities/ — discovery list of visible synthetic opps."""

    @extend_schema(
        summary="List synthetic opportunities visible to the token user",
        responses=ExportPageSerializer,
        parameters=_PAGE_PARAMS,
    )
    def get(self, request):
        results = []
        for opp in SyntheticOpportunity.objects.filter(labs_only=True, enabled=True):
            if not opp.is_visible_to(request.user):
                continue
            rows = _synthetic_client(opp.opportunity_id).fetch_all("")
            if rows:
                results.append(rows[0])
        paginator = IdKeysetPagination()
        page = paginator.paginate_queryset(results, request, view=self)
        return paginator.get_paginated_response(page)


class OpportunityDetailView(_ExportView):
    """GET /api/export/opportunity/<id>/ — bare opportunity dict."""

    @extend_schema(
        summary="Opportunity detail",
        responses={200: OpenApiTypes.OBJECT, 404: OpenApiResponse(description="Not found or not visible.")},
    )
    def get(self, request, opportunity_id):
        opp = _visible_opp_or_404(request.user, opportunity_id)
        rows = _synthetic_client(opportunity_id).fetch_all("")
        if not rows:
            raise NotFound("Opportunity not found.")
        detail = dict(rows[0])
        # #650 gap 5 — Scout uses visit_count as the visit-progress denominator.
        detail["visit_count"] = opp.visit_count if opp.visit_count is not None else detail.get("visit_count", 0)
        return Response(detail)


class OpportunityDataView(_ExportView):
    """GET a paginated export endpoint (user_visits / user_data / ...).

    The fixture endpoint key is bound per-URL via ``as_view(endpoint=...)``.
    """

    endpoint = None

    @extend_schema(
        summary="Paginated export endpoint",
        responses=ExportPageSerializer,
        parameters=_PAGE_PARAMS,
    )
    def get(self, request, opportunity_id):
        _visible_opp_or_404(request.user, opportunity_id)
        rows = _synthetic_client(opportunity_id).fetch_all(self.endpoint)
        paginator = IdKeysetPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(page)


class AppStructureView(_ExportView):
    """GET /api/export/opportunity/<id>/app_structure/.

    Mirrors real Connect: always returns the ``{"learn_app", "deliver_app"}``
    wrapper (each value is the app JSON or null), honoring
    ``?app_type=learn|deliver|both`` (default ``both``). An opp with no app
    fixture returns 200 with both keys null. An invalid app_type returns 400.
    """

    @extend_schema(
        summary="App structure (learn and/or deliver)",
        parameters=[
            OpenApiParameter(
                "app_type",
                OpenApiTypes.STR,
                enum=list(_VALID_APP_TYPES),
                description="Which app(s) to include: learn, deliver, or both (default both).",
            )
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiResponse(description="Invalid app_type.")},
    )
    def get(self, request, opportunity_id):
        _visible_opp_or_404(request.user, opportunity_id)
        app_type = request.query_params.get("app_type", _APP_TYPE_BOTH)
        if app_type not in _VALID_APP_TYPES:
            return Response(
                {"error": f"Invalid app_type. Must be one of: {', '.join(_VALID_APP_TYPES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = _synthetic_client(opportunity_id).fetch_all("app_structure")
        wrapper = rows[0] if rows and isinstance(rows[0], dict) else {}
        include_learn = app_type in (_APP_TYPE_LEARN, _APP_TYPE_BOTH)
        include_deliver = app_type in (_APP_TYPE_DELIVER, _APP_TYPE_BOTH)
        return Response(
            {
                "learn_app": wrapper.get("learn_app") if include_learn else None,
                "deliver_app": wrapper.get("deliver_app") if include_deliver else None,
            }
        )


class OppOrgProgramListView(_ExportView):
    """GET /api/export/opp_org_program_list/ — org/program/opp tree (purely synthetic).

    Mirrors production Connect's ``ProgramOpportunityOrganizationDataView`` shape so an
    external consumer's metadata loader works unchanged. Built ONLY from synthetic opps
    visible to the token user — never reads session/real-Connect org data. Org slugs and
    program ids come from ``labs.synthetic.org_tree`` (shared with labs_context).
    """

    @extend_schema(
        summary="Org / program / opportunity tree for visible synthetic opps",
        responses=inline_serializer(
            "SyntheticOppOrgProgramList",
            {
                "organizations": serializers.ListField(child=serializers.DictField()),
                "opportunities": serializers.ListField(child=serializers.DictField()),
                "programs": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def get(self, request):
        organizations: dict[str, dict] = {}
        programs: dict[int, dict] = {}
        opportunities = []

        for opp in SyntheticOpportunity.objects.filter(labs_only=True, enabled=True):
            if not opp.is_visible_to(request.user):
                continue
            org_slug = synthetic_org_slug(opp)
            org_name = opp.org_name or "Labs Synthetic"
            program_id = synthetic_program_id(opp)
            program_name = opp.program_name or "Labs Synthetic"

            detail_rows = _synthetic_client(opp.opportunity_id).fetch_all("")
            detail = detail_rows[0] if detail_rows and isinstance(detail_rows[0], dict) else {}

            organizations.setdefault(org_slug, {"id": org_slug, "slug": org_slug, "name": org_name})
            programs.setdefault(
                program_id,
                {
                    "id": program_id,
                    "name": program_name,
                    "delivery_type": None,
                    "currency": None,
                    "organization": org_slug,
                },
            )
            opportunities.append(
                {
                    "id": opp.opportunity_id,
                    "name": detail.get("name") or opp.label or f"Synthetic {opp.opportunity_id}",
                    "date_created": detail.get("date_created") or opp.created_at.isoformat(),
                    "organization": org_slug,
                    "end_date": detail.get("end_date"),
                    "is_active": detail.get("is_active", True),
                    # Intentional divergence from production: real Connect's
                    # OpportunityDataExportSerializer.get_program returns None for
                    # standalone (non-managed) opps.  Synthetic opps always belong to
                    # a synthetic program, so program_id always resolves in programs[].
                    "program": program_id,
                    "visit_count": opp.visit_count or 0,
                }
            )

        return Response(
            {
                "organizations": list(organizations.values()),
                "opportunities": opportunities,
                "programs": list(programs.values()),
            }
        )
