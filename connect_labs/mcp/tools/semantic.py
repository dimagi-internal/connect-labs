# connect_labs/mcp/tools/semantic.py
"""Semantic-registry tools: iterate on INDICATORS without a deploy.

The pipelines were always live code -- `pipeline_update_schema` changes an
extraction and the next run uses it. The indicators over those pipelines were
not: they lived in YAML in the repo, so moving a band, fixing a mapping or adding
a measure meant a pull request, a merge and a deploy. These tools close that gap
using the same records the pipeline tools use.

`semantic_registry_validate` is the one worth reaching for first. It answers "is
this registry safe" WITHOUT saving it, so a wrong edit costs a round trip instead
of a broken dashboard.
"""

import logging

from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import RegistryInvalid, validate_registry
from connect_labs.workflow.data_access import SemanticRegistryDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

_SCOPE_PROPS = {
    "opportunity_id": {"type": "integer"},
    "program_id": {"type": "integer"},
    "organization_id": {"type": "integer"},
}


def _access(user, opportunity_id=None, program_id=None, organization_id=None):
    return SemanticRegistryDataAccess(
        access_token=require_connect_token(user),
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )


def _summary(record):
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "version": record.version,
        "is_shared": record.is_shared,
        "measures": len(record.indicators_doc.get("measures") or []),
        "properties": len(record.properties_doc.get("properties") or []),
    }


@register(
    name="semantic_registry_list",
    description=(
        "List semantic registries (indicator definitions) visible to the caller, including "
        "shared ones. Returns summaries; use semantic_registry_get for the full documents."
    ),
    input_schema={"type": "object", "properties": dict(_SCOPE_PROPS), "additionalProperties": False},
)
def semantic_registry_list(user, opportunity_id=None, program_id=None, organization_id=None):
    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        return {"registries": [_summary(r) for r in access.list_registries(include_shared=True)]}
    finally:
        access.close()


@register(
    name="semantic_registry_get",
    description=(
        "Fetch one semantic registry in full: `properties` (Layer 2, the case-level "
        "properties), `indicators` (Layer 3, the measures and suppression rules) and "
        "`deployment` (the llo map and credibility gates the compiler cannot derive)."
    ),
    input_schema={
        "type": "object",
        "properties": {"registry_id": {"type": "integer"}, **_SCOPE_PROPS},
        "required": ["registry_id"],
        "additionalProperties": False,
    },
)
def semantic_registry_get(user, registry_id: int, opportunity_id=None, program_id=None, organization_id=None):
    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        record = access.get_registry(registry_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry with id {registry_id}")
        return {
            **_summary(record),
            "properties_doc": record.properties_doc,
            "indicators_doc": record.indicators_doc,
            "deployment": record.deployment,
        }
    finally:
        access.close()


@register(
    name="semantic_registry_validate",
    description=(
        "Check whether a registry would be accepted, WITHOUT saving it. Resolves every "
        "reference, parses each SQL fragment against the expression allow-list, and compiles "
        "at every scope with the gates attached. Returns {valid, errors}. Call this before "
        "semantic_registry_update to keep a bad edit from reaching a dashboard."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "properties_doc": {"type": "object"},
            "indicators_doc": {"type": "object"},
            "deployment": {"type": "object"},
        },
        "required": ["properties_doc", "indicators_doc"],
        "additionalProperties": False,
    },
)
def semantic_registry_validate(user, properties_doc: dict, indicators_doc: dict, deployment: dict | None = None):
    errors = validate_registry(properties_doc, indicators_doc, deployment)
    return {"valid": not errors, "errors": errors}


@register(
    name="semantic_registry_create",
    description=(
        "Create a semantic registry. Pass `seed_from` (e.g. 'kmc') to copy one of the "
        "built-in on-disk registries as the starting point, or supply the documents "
        "directly. Refuses anything that does not validate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "seed_from": {"type": "string"},
            "properties_doc": {"type": "object"},
            "indicators_doc": {"type": "object"},
            "deployment": {"type": "object"},
            "is_shared": {"type": "boolean"},
            **_SCOPE_PROPS,
        },
        "additionalProperties": False,
    },
)
def semantic_registry_create(
    user,
    name=None,
    description="",
    seed_from=None,
    properties_doc=None,
    indicators_doc=None,
    deployment=None,
    is_shared=False,
    opportunity_id=None,
    program_id=None,
    organization_id=None,
):
    if seed_from:
        try:
            payload = registry_payload(seed_from)
        except Exception as exc:
            raise MCPToolError("INVALID_SCHEMA", f"cannot seed from {seed_from!r}: {exc}") from exc
        properties_doc = properties_doc or payload["properties"]
        indicators_doc = indicators_doc or payload["indicators"]
        deployment = deployment if deployment is not None else payload["deployment"]
        name = name or payload["name"]
        description = description or payload["description"]

    if not properties_doc or not indicators_doc:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "supply properties_doc and indicators_doc, or seed_from to copy a built-in registry.",
        )

    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        record = access.create_registry(
            name=name or "Untitled Registry",
            description=description,
            properties=properties_doc,
            indicators=indicators_doc,
            deployment=deployment,
            is_shared=bool(is_shared),
        )
    except RegistryInvalid as exc:
        raise MCPToolError("INVALID_SCHEMA", "registry rejected:\n  " + "\n  ".join(exc.errors)) from exc
    finally:
        access.close()
    return _summary(record)


@register(
    name="semantic_registry_update",
    description=(
        "Patch a semantic registry. Any document you omit is left alone; validation runs "
        "against the MERGED result, so a change to one document cannot silently break "
        "another. A definition change bumps the version."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "registry_id": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "properties_doc": {"type": "object"},
            "indicators_doc": {"type": "object"},
            "deployment": {"type": "object"},
            "is_shared": {"type": "boolean"},
            **_SCOPE_PROPS,
        },
        "required": ["registry_id"],
        "additionalProperties": False,
    },
)
def semantic_registry_update(
    user,
    registry_id: int,
    name=None,
    description=None,
    properties_doc=None,
    indicators_doc=None,
    deployment=None,
    is_shared=None,
    opportunity_id=None,
    program_id=None,
    organization_id=None,
):
    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        before = access.get_registry(registry_id)
        if before is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry with id {registry_id}")
        record = access.update_registry(
            registry_id,
            name=name,
            description=description,
            properties=properties_doc,
            indicators=indicators_doc,
            deployment=deployment,
            is_shared=is_shared,
        )
    except RegistryInvalid as exc:
        raise MCPToolError("INVALID_SCHEMA", "registry rejected:\n  " + "\n  ".join(exc.errors)) from exc
    finally:
        access.close()
    return {**_summary(record), "_version_before": before.version, "_version_after": record.version}
