"""MCP tools for the labs synthetic-data system."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.synthetic.gdrive import DriveClient
from commcare_connect.labs.synthetic.generator.engine import generate as _generate
from commcare_connect.labs.synthetic.generator.manifest import (
    Manifest,
    ManifestValidationError,
)
from commcare_connect.labs.synthetic.generator.schema_loader import FormSchema
from commcare_connect.labs.synthetic.generator.uploader import upload_and_register
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


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
                "synthetic_generate_from_manifest: upstream RequestError loading opp %s: %s; "
                "falling back to stub.",
                opportunity_id,
                exc,
            )
            return fallback
        if resp.status_code >= 400:
            logger.warning(
                "synthetic_generate_from_manifest: opp_detail GET %s returned %s; "
                "falling back to stub.",
                url,
                resp.status_code,
            )
            return fallback
        return resp.json()
    finally:
        client.close()


def _load_form_schema_for_opp(opportunity_id: int, user) -> FormSchema:
    """Resolve the opp's primary form schema.

    For v1 this is intentionally an empty schema. The labs server does not
    yet expose a Python wrapper for the HQ ``get_form_json_paths`` query
    used by the local commcare_hq_mcp stdio server, and the engine's field
    filler tolerates an empty schema (no per-question form_json fields are
    added, but every visit still carries the standard metadata fields).

    TODO(plan-B): wire a server-side HQ schema fetch. The likely path is to
    call ``/export/opportunity/<id>/app_structure/`` (already exposed via
    ``get_opportunity_apps``) and translate the deliver app's primary form
    into ``QuestionSpec`` entries.
    """
    return FormSchema(questions=[])


@register(
    name="synthetic_generate_from_manifest",
    description=(
        "Generate the five fixture JSON files from a YAML manifest, upload "
        "them to a fresh GDrive folder, and register the opportunity as "
        "synthetic. Returns the new folder_id and per-endpoint record counts."
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
    try:
        manifest = Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    if manifest.opportunity_id != opportunity_id:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"manifest.opportunity_id ({manifest.opportunity_id}) != "
            f"tool arg opportunity_id ({opportunity_id})",
        )

    detail = _load_opportunity_detail(opportunity_id, user)
    form_schema = _load_form_schema_for_opp(opportunity_id, user)
    fixtures = _generate(
        manifest=manifest, opportunity_detail=detail, form_schema=form_schema
    )
    drive = DriveClient()
    result = upload_and_register(
        drive=drive,
        opportunity_id=opportunity_id,
        opportunity_name=manifest.opportunity_name,
        fixtures=fixtures,
    )
    return {
        "folder_id": result.folder_id,
        "record_counts": result.record_counts,
        "form_schema_questions": len(form_schema.questions),
    }
