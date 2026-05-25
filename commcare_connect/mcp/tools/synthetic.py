"""MCP tools for the labs synthetic-data system."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.synthetic.dump import _fetch_endpoint
from commcare_connect.labs.synthetic.gdrive import DriveClient
from commcare_connect.labs.synthetic.generator.engine import generate as _generate
from commcare_connect.labs.synthetic.generator.manifest import Manifest, ManifestValidationError
from commcare_connect.labs.synthetic.generator.profiler import profile as _profile
from commcare_connect.labs.synthetic.generator.schema_loader import FormSchema, parse_form_schema_from_app_json
from commcare_connect.labs.synthetic.generator.uploader import upload_and_register
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


def _accessible_opp_ids_for_user(user) -> set[int]:
    """Return the set of opportunity IDs the user has Connect access to.

    The labs UI's ``registry.accessible_opp_ids(request)`` reads the org
    data the OAuth callback stashed in the user's session. MCP tools don't
    have a request, so we fetch the same data fresh from production using
    the user's stored Connect access token. This is the same upstream
    endpoint (``/export/opp_org_program_list/``) the OAuth callback hits.

    Returns an empty set if the user has no Connect token, or if the
    upstream call fails — callers must treat empty as "deny everything".
    """
    from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data

    try:
        token = require_connect_token(user)
    except MCPToolError:
        return set()

    org_data = fetch_user_organization_data(token) or {}
    return {int(o["id"]) for o in org_data.get("opportunities", []) if o.get("id") is not None}


def _require_opportunity_access(user, opportunity_id: int) -> None:
    """Raise PERMISSION_DENIED if the user has no access to ``opportunity_id``.

    Two paths are accepted:
    1. The opp is a labs-only SyntheticOpportunity the user can see (via
       ``view_synthetic_opps`` + matching ``allowed_domains``). These opps
       have no Connect side and are gated entirely on the labs visibility model.
    2. The opp is in the user's live Connect membership data (the existing
       check — same source the labs synthetic UI uses, just without the
       request-bound session detour). Empty set (no token, upstream failure)
       is treated as "no access" so an unauthenticated caller can't slip a
       write through.
    """
    # Labs-only path first — cheap DB lookup, no upstream call.
    try:
        opp = SyntheticOpportunity.objects.get(opportunity_id=opportunity_id, labs_only=True)
    except SyntheticOpportunity.DoesNotExist:
        opp = None
    if opp is not None and opp.is_visible_to(user):
        return

    accessible = _accessible_opp_ids_for_user(user)
    if opportunity_id not in accessible:
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"opportunity_id {opportunity_id} is not in your accessible set",
        )


@register(
    name="synthetic_register",
    description=(
        "Register or update a synthetic-opportunity entry. Set enabled=True "
        "to make labs serve fixtures from the given GDrive folder for this "
        "opportunity_id; set enabled=False to disable without deleting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "gdrive_folder_id": {"type": "string"},
            "enabled": {"type": "boolean", "default": True},
            "label": {"type": ["string", "null"], "default": None},
        },
        "required": ["opportunity_id", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_register(
    user,
    *,
    opportunity_id: int,
    gdrive_folder_id: str,
    enabled: bool = True,
    label: str | None = None,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    defaults: dict[str, Any] = {
        "gdrive_folder_id": gdrive_folder_id,
        "enabled": enabled,
        "created_by": user,
    }
    if label is not None:
        defaults["label"] = label
    row, _created = SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults=defaults,
    )
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
        "label": row.label,
    }


@register(
    name="synthetic_disable",
    description=(
        "Disable a synthetic-opportunity entry without deleting it. The "
        "GDrive folder is retained for forensics; labs reverts to real "
        "export reads for this opportunity_id on next request."
    ),
    input_schema={
        "type": "object",
        "properties": {"opportunity_id": {"type": "integer"}},
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_disable(user, *, opportunity_id: int) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    try:
        row = SyntheticOpportunity.objects.get(opportunity_id=opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError("NOT_FOUND", f"No synthetic entry for opportunity_id={opportunity_id}")
    row.enabled = False
    row.save(update_fields=["enabled", "updated_at"])
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
    }


def _load_opportunity_detail(opportunity_id: int, user) -> dict:
    """Pull live opportunity detail from prod via the user's OAuth token.

    Uses the same /export/opportunity/<id>/ endpoint that the labs explorer's
    AppDownloaderDataAccess.get_opportunity_details hits, authenticated with
    the calling user's stored Connect access token.

    Falls back to a minimal stub if the user has no token, the upstream call
    fails, or the user lacks access to the opportunity. The engine tolerates
    an empty payload and still produces fixtures (no payment_units / no
    deliver_unit_id, but every visit still gets the standard 23 metadata
    fields).
    """
    fallback: dict[str, Any] = {
        "id": opportunity_id,
        "name": "(synthetic)",
        "payment_units": [],
        "deliver_units": [],
    }
    try:
        token = require_connect_token(user)
    except MCPToolError:
        logger.warning(
            "synthetic_generate_from_manifest: no Connect token for user; "
            "using empty opportunity_detail stub for opp_id=%s",
            opportunity_id,
        )
        return fallback

    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/"
        try:
            resp = client.http_client.get(url, timeout=60.0)
        except httpx.RequestError as exc:
            logger.warning(
                "synthetic_generate_from_manifest: upstream RequestError loading opp %s: %s; " "falling back to stub.",
                opportunity_id,
                exc,
            )
            return fallback
        if resp.status_code >= 400:
            logger.warning(
                "synthetic_generate_from_manifest: opp_detail GET %s returned %s; " "falling back to stub.",
                url,
                resp.status_code,
            )
            return fallback
        return resp.json()
    finally:
        client.close()


def _load_form_schema_for_opp(opportunity_id: int, user) -> FormSchema:
    """Resolve the opp's primary deliver form schema by hitting Connect's app_structure endpoint.

    Calls ``/export/opportunity/<id>/app_structure/?app_type=deliver`` (the same
    upstream the ``get_opportunity_apps`` MCP tool uses) and translates the
    deliver app's primary form into ``QuestionSpec`` entries via
    ``parse_form_schema_from_app_json``.

    Falls back to an empty FormSchema if the user has no Connect token, the
    upstream call fails, or the opp has no deliver app. The engine's field
    filler tolerates an empty schema (no per-question form_json fields are
    added, but every visit still carries the standard 23 metadata fields).
    """
    try:
        token = require_connect_token(user)
    except MCPToolError:
        logger.warning(
            "synthetic_generate_from_manifest: no Connect token for user; " "using empty form_schema for opp_id=%s",
            opportunity_id,
        )
        return FormSchema(questions=[])

    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/app_structure/"
        try:
            resp = client.http_client.get(url, params={"app_type": "deliver"}, timeout=120.0)
        except httpx.RequestError as exc:
            logger.warning(
                "synthetic_generate_from_manifest: upstream RequestError loading app_structure for opp %s: %s; "
                "falling back to empty schema.",
                opportunity_id,
                exc,
            )
            return FormSchema(questions=[])
        if resp.status_code >= 400:
            logger.warning(
                "synthetic_generate_from_manifest: app_structure GET %s returned %s; falling back to empty schema.",
                url,
                resp.status_code,
            )
            return FormSchema(questions=[])
        return parse_form_schema_from_app_json(resp.json(), app_type="deliver")
    finally:
        client.close()


@register(
    name="synthetic_generate_from_manifest",
    description=(
        "Generate the five fixture JSON files from a YAML manifest, upload "
        "them to a fresh GDrive folder, and register the opportunity as "
        "synthetic. Returns the new folder_id, a human-openable folder_url, "
        "and per-endpoint record counts so callers can verify the upload."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "manifest_yaml": {"type": "string"},
        },
        "required": ["opportunity_id", "manifest_yaml"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_generate_from_manifest(
    user,
    *,
    opportunity_id: int,
    manifest_yaml: str,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    try:
        manifest = Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    if manifest.opportunity_id != opportunity_id:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"manifest.opportunity_id ({manifest.opportunity_id}) != " f"tool arg opportunity_id ({opportunity_id})",
        )

    detail = _load_opportunity_detail(opportunity_id, user)
    form_schema = _load_form_schema_for_opp(opportunity_id, user)
    fixtures = _generate(manifest=manifest, opportunity_detail=detail, form_schema=form_schema)
    drive = DriveClient()
    result = upload_and_register(
        drive=drive,
        opportunity_id=opportunity_id,
        opportunity_name=manifest.opportunity_name,
        fixtures=fixtures,
    )

    task_records = fixtures.get("task_records", [])
    tasks_created = 0
    if task_records:
        # For labs-only opps the client has no Connect token; the dispatch in
        # LabsRecordAPIClient routes writes to LabsLocalRecord instead. Pass
        # token=None (won't be used) rather than require_connect_token which
        # would raise for users without a Connect membership.
        try:
            token = require_connect_token(user)
        except MCPToolError:
            token = ""
        client = LabsRecordAPIClient(access_token=token, opportunity_id=opportunity_id)
        try:
            for rec in task_records:
                # Write as Task records so the Tasks UI (experiment="tasks",
                # type="Task") picks them up. The synthetic generator already
                # produces records in the Task schema; this just registers them
                # under the right experiment/type tags.
                client.create_record(
                    experiment="tasks",
                    type="Task",
                    data=rec,
                    username=rec.get("username") or "",
                )
                tasks_created += 1
        finally:
            client.close()

    # Invalidate the labs analysis SQL cache so the next pipeline read sees the
    # fresh visits/fixtures we just uploaded — otherwise stale aggregated cache
    # from a prior fixture set keeps shadowing the new data.
    from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager
    from commcare_connect.labs.synthetic.registry import invalidate_cache as _reg_invalidate

    SQLCacheManager.delete_all_cache(opportunity_id)
    _reg_invalidate()

    return {
        "folder_id": result.folder_id,
        "folder_url": result.folder_url,
        "record_counts": result.record_counts,
        "form_schema_questions": len(form_schema.questions),
        "tasks_created": tasks_created,
    }


@register(
    name="synthetic_create_labs_only",
    description=(
        "Create a labs-only synthetic opportunity from scratch. No real Connect "
        "opp is required — opportunity_id is auto-allocated from the labs-only "
        "reserved range (10_000+). The opp is surfaced into labs_context only "
        "for users with view_synthetic_opps=True whose email domain matches one "
        "of allowed_domains. Returns the new opportunity_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "gdrive_folder_id": {"type": "string"},
            "org_name": {"type": "string", "default": "Labs Synthetic"},
            "program_name": {"type": "string", "default": "Labs Synthetic"},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["@dimagi.com"],
                "description": "Email-domain allowlist (e.g. ['@dimagi.com']). Empty = any.",
            },
            "enabled": {"type": "boolean", "default": True},
            "notes": {"type": "string", "default": ""},
        },
        "required": ["label", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_create_labs_only(
    user,
    *,
    label: str,
    gdrive_folder_id: str,
    org_name: str = "Labs Synthetic",
    program_name: str = "Labs Synthetic",
    allowed_domains: list[str] | None = None,
    enabled: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    new_opp_id = SyntheticOpportunity.next_labs_only_opp_id()
    row = SyntheticOpportunity.objects.create(
        opportunity_id=new_opp_id,
        label=label,
        gdrive_folder_id=gdrive_folder_id,
        org_name=org_name,
        program_name=program_name,
        allowed_domains=allowed_domains if allowed_domains is not None else ["@dimagi.com"],
        enabled=enabled,
        notes=notes,
        labs_only=True,
        created_by=user,
    )
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "label": row.label,
        "gdrive_folder_id": row.gdrive_folder_id,
        "org_name": row.org_name,
        "program_name": row.program_name,
        "allowed_domains": list(row.allowed_domains),
        "labs_only": True,
        "enabled": row.enabled,
    }


@register(
    name="synthetic_clone_to_labs_only",
    description=(
        "Clone an existing SyntheticOpportunity (real-backed or labs-only) into a "
        "new labs-only opp. Reuses the source's gdrive_folder_id (same fixture set, "
        "new opp_id from the 10_000+ range). Open to any authenticated MCP caller: "
        "once a source has been registered as a SyntheticOpportunity it's already a "
        "labs-controlled fixture artifact, so cloning it doesn't grant any new data "
        "access — it just creates a second view onto the same GDrive fixture folder. "
        "Use this to make existing synthetic fixture data accessible to users who "
        "lack Connect membership for the original opp (e.g. ACE)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_opportunity_id": {"type": "integer"},
            "label": {
                "type": ["string", "null"],
                "default": None,
                "description": "Label for the new opp. Defaults to 'Clone of <source label>'.",
            },
            "org_name": {"type": ["string", "null"], "default": None},
            "program_name": {"type": ["string", "null"], "default": None},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["@dimagi.com", "@dimagi-ai.com"],
                "description": (
                    "Email-domain allowlist for the new labs-only opp. Default is broad "
                    "(['@dimagi.com', '@dimagi-ai.com']) so ace@dimagi-ai.com can use it."
                ),
            },
        },
        "required": ["source_opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_clone_to_labs_only(
    user,
    *,
    source_opportunity_id: int,
    label: str | None = None,
    org_name: str | None = None,
    program_name: str | None = None,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    try:
        source = SyntheticOpportunity.objects.get(opportunity_id=source_opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError(
            "NOT_FOUND",
            f"No SyntheticOpportunity for opportunity_id={source_opportunity_id}. "
            "Register the source as synthetic first via synthetic_register or "
            "synthetic_generate_from_manifest.",
        )

    # Auth: any authenticated MCP caller may clone an existing SyntheticOpportunity.
    # The source row's existence is the gate — it was registered by a human with
    # Connect access, the underlying data is already a synthetic fixture, and the
    # clone creates only a second view onto the same GDrive folder (no new data).
    # Visibility of the new opp is controlled by allowed_domains + view_synthetic_opps.
    new_opp_id = SyntheticOpportunity.next_labs_only_opp_id()
    row = SyntheticOpportunity.objects.create(
        opportunity_id=new_opp_id,
        label=label or f"Clone of {source.label or source.opportunity_id}",
        gdrive_folder_id=source.gdrive_folder_id,
        org_name=org_name or source.org_name or "Labs Synthetic",
        program_name=program_name or source.program_name or "Labs Synthetic",
        allowed_domains=(allowed_domains if allowed_domains is not None else ["@dimagi.com", "@dimagi-ai.com"]),
        enabled=True,
        notes=f"Cloned from opp {source_opportunity_id} via MCP.",
        labs_only=True,
        created_by=user,
    )
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "source_opportunity_id": source_opportunity_id,
        "label": row.label,
        "gdrive_folder_id": row.gdrive_folder_id,
        "org_name": row.org_name,
        "program_name": row.program_name,
        "allowed_domains": list(row.allowed_domains),
        "labs_only": True,
    }


@register(
    name="synthetic_set_my_visibility",
    description=(
        "Toggle the calling user's `view_synthetic_opps` setting. When on, "
        "labs-only synthetic opportunities whose `allowed_domains` matches the "
        "user's email domain are merged into the user's labs_context (org/"
        "program/opportunity lists). Off by default. Returns the new state."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "True to opt in to seeing labs-only opps; False to opt out.",
            },
        },
        "required": ["enabled"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_set_my_visibility(user, *, enabled: bool) -> dict[str, Any]:
    user.view_synthetic_opps = bool(enabled)
    user.save(update_fields=["view_synthetic_opps"])
    return {
        "view_synthetic_opps": user.view_synthetic_opps,
        "email": user.email,
    }


@register(
    name="synthetic_profile_from_prod",
    description=(
        "Analyze real production data for an opportunity and produce a "
        "synthetic-data manifest that reproduces the same statistical shape. "
        "Reads the five export endpoints server-side, computes per-FLW "
        "distributions (approval rates, flag rates, visit cadence), field "
        "value distributions from form_json, and timeline parameters. "
        "Returns a YAML manifest string (no PII) ready to pass to "
        "synthetic_generate_from_manifest."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "form_json_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional explicit list of form_json dot-paths to profile "
                    "(e.g. ['form.case.update.soliciter_muac_cm']). If omitted, "
                    "auto-discovers numeric fields from a sample of visits."
                ),
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_profile_from_prod(
    user,
    *,
    opportunity_id: int,
    form_json_paths: list[str] | None = None,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)

    try:
        token = require_connect_token(user)
    except MCPToolError:
        raise MCPToolError(
            "PERMISSION_DENIED",
            "No Connect token available — cannot fetch production data.",
        )

    base_url = settings.CONNECT_PRODUCTION_URL

    logger.info("synthetic_profile_from_prod: fetching exports for opp %s", opportunity_id)
    detail = _fetch_endpoint(base_url, opportunity_id, "", token)
    user_visits = _fetch_endpoint(base_url, opportunity_id, "user_visits", token)
    user_data = _fetch_endpoint(base_url, opportunity_id, "user_data", token)

    if not isinstance(user_visits, list) or not user_visits:
        raise MCPToolError(
            "NOT_FOUND",
            f"No user_visits data for opportunity_id={opportunity_id}",
        )

    logger.info(
        "synthetic_profile_from_prod: profiling %d visits, %d users for opp %s",
        len(user_visits),
        len(user_data) if isinstance(user_data, list) else 0,
        opportunity_id,
    )

    manifest_yaml = _profile(
        opportunity_id=opportunity_id,
        user_visits=user_visits,
        user_data=user_data if isinstance(user_data, list) else [],
        opportunity_detail=detail if isinstance(detail, dict) else {},
        form_json_paths=form_json_paths,
    )

    return {
        "manifest_yaml": manifest_yaml,
        "source_visit_count": len(user_visits),
        "source_flw_count": len({v.get("username") for v in user_visits if v.get("username")}),
        "source_entity_count": len({v.get("entity_id") for v in user_visits if v.get("entity_id")}),
    }
