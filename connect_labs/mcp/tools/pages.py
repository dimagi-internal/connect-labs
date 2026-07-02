"""MCP tools for authoring pages surfaces (composable card landing pages)."""

from __future__ import annotations

import logging

from connect_labs.pages.data_access import SurfaceDataAccess, resolve_surface
from connect_labs.pages.providers import list_providers

from ..connect_token import require_connect_token
from ..tool_registry import register

logger = logging.getLogger(__name__)


def _coerce_id(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@register(
    name="pages_list_providers",
    description="List available card providers (audit, workflow-declared, …) with their target_kind and label.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    is_write=False,
)
def pages_list_providers(user):
    providers = [{"key": p.key, "label": p.label, "target_kind": p.target_kind} for p in list_providers()]
    return {"providers": providers}


@register(
    name="pages_list",
    description="List surfaces scoped to a program_id or opportunity_id.",
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {"type": "string"},
            "opportunity_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
    is_write=False,
)
def pages_list(user, program_id=None, opportunity_id=None):
    token = require_connect_token(user)
    da = SurfaceDataAccess(
        access_token=token, program_id=_coerce_id(program_id), opportunity_id=_coerce_id(opportunity_id)
    )
    return {"surfaces": da.list_surfaces()}


@register(
    name="pages_get",
    description="Get a surface by slug, resolved against scope context (opportunity/program/org), then public.",
    input_schema={
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "opportunity_id": {"type": "string"},
            "program_id": {"type": "string"},
            "organization_id": {"type": "string"},
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
    is_write=False,
)
def pages_get(user, slug, opportunity_id=None, program_id=None, organization_id=None):
    token = require_connect_token(user)
    context = {}
    if _coerce_id(opportunity_id):
        context["opportunity_id"] = _coerce_id(opportunity_id)
    if _coerce_id(program_id):
        context["program_id"] = _coerce_id(program_id)
    if _coerce_id(organization_id):
        context["organization_id"] = _coerce_id(organization_id)
    return {"surface": resolve_surface(token, context, slug)}


@register(
    name="pages_create",
    description=(
        "Create a surface (card landing page). `cards` is a list of "
        '{"provider","target","options"}. Scope with exactly one of opportunity_id / '
        "program_id / organization_id, or set public=true for a public page."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "title": {"type": "string"},
            "cards": {"type": "array", "items": {"type": "object"}},
            "options": {"type": "object"},
            "program_id": {"type": "string"},
            "opportunity_id": {"type": "string"},
            "organization_id": {"type": "string"},
            "public": {"type": "boolean"},
        },
        "required": ["slug", "title", "cards"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pages_create(
    user, slug, title, cards, options=None, program_id=None, opportunity_id=None, organization_id=None, public=False
):
    token = require_connect_token(user)
    da = SurfaceDataAccess(
        access_token=token,
        program_id=_coerce_id(program_id),
        opportunity_id=_coerce_id(opportunity_id),
        organization_id=_coerce_id(organization_id),
    )
    return da.create_surface(slug=slug, title=title, cards=cards, options=options or {}, public=bool(public))


@register(
    name="pages_update",
    description=(
        "Update an existing surface by record id. Scope with exactly one of "
        "opportunity_id / program_id / organization_id, or set public=true for a public page."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "record_id": {"type": "integer"},
            "slug": {"type": "string"},
            "title": {"type": "string"},
            "cards": {"type": "array", "items": {"type": "object"}},
            "options": {"type": "object"},
            "program_id": {"type": "string"},
            "opportunity_id": {"type": "string"},
            "organization_id": {"type": "string"},
            "public": {"type": "boolean"},
        },
        "required": ["record_id", "slug", "title", "cards"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pages_update(
    user,
    record_id,
    slug,
    title,
    cards,
    options=None,
    program_id=None,
    opportunity_id=None,
    organization_id=None,
    public=False,
):
    token = require_connect_token(user)
    da = SurfaceDataAccess(
        access_token=token,
        program_id=_coerce_id(program_id),
        opportunity_id=_coerce_id(opportunity_id),
        organization_id=_coerce_id(organization_id),
    )
    return da.update_surface(
        record_id=record_id, slug=slug, title=title, cards=cards, options=options or {}, public=bool(public)
    )
