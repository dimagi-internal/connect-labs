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

from connect_labs.semantic.explain import UnknownIndicator, english, explain
from connect_labs.semantic.runtime import normalise_deployment_facts
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


def _indicator_index(record) -> list[dict]:
    """Every top-level indicator as one line: what it is, not how it is computed.

    Deliberately cheap -- `english` renders from the registry documents and
    compiles no SQL, so an index of the whole registry costs about what one
    explanation used to.
    """
    rows = []
    for measure in record.indicators_doc.get("measures") or []:
        meta = measure.get("meta") or {}
        if not meta.get("indicator"):
            continue
        rows.append(
            {
                "indicator": meta["indicator"],
                "measure": measure["name"],
                "title": measure.get("title"),
                "unit": meta.get("unit"),
                "category": meta.get("category"),
                # Both readings, as `explain` returns them: `plain` is the authored
                # wording where the registry has one, `definition` is rendered from
                # the SQL and so cannot drift from the number.
                "plain": meta.get("plain"),
                "definition": (english(record.indicators_doc, record.properties_doc, measure["name"]) or {}).get(
                    "definition"
                ),
            }
        )
    return rows


@register(
    name="semantic_registry_explain",
    description=(
        "The exact logic behind an indicator, read from the registry with nothing hidden: "
        "the compiled measure expression, its numerator/denominator components, the Layer-2 "
        "property chain it depends on in evaluation order with every constant substituted, "
        "the per-baby aggregates and weight-series window derivations it touches, and the "
        "section-2 cutoffs used. The full compiled statement for the scope comes back ONCE, "
        "beside the indicators (Layer 1, the pipeline rows, is a named placeholder -- read "
        "that schema with pipeline_get). Pass an indicator id (N15, C14), a measure name "
        "(n15), or several. This is how a second engine reproduces a number instead of "
        "trusting its label. Omit `indicators` for an INDEX of every top-level indicator -- "
        "id, title and one-line definition -- then ask again by id for the ones you want; the "
        "chains are far too big to return all at once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "registry_id": {"type": "integer"},
            "indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Indicator ids or measure names, e.g. ['N15', 'C14']. Omit for an index of every "
                    "top-level indicator (id, title, one-line definition) rather than their full chains."
                ),
            },
            "scope": {
                "type": "string",
                "description": "Scope the compiled statement groups by: programme (default), llo, opportunity, flw.",
            },
            **_SCOPE_PROPS,
        },
        "required": ["registry_id"],
        "additionalProperties": False,
    },
)
def semantic_registry_explain(
    user,
    registry_id: int,
    indicators: list[str] | None = None,
    scope: str = "programme",
    opportunity_id=None,
    program_id=None,
    organization_id=None,
):
    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        record = access.get_registry(registry_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry with id {registry_id}")
        facts = normalise_deployment_facts(record.deployment)
        if not indicators:
            # An index, not every chain. Explaining all of them returned ~1.17M
            # characters for the KMC registry -- 37 indicators, each carrying its
            # own copy of one 27KB compiled statement -- which is more than ten
            # times what a client will accept in a single tool result, so the
            # obvious first call ("explain everything") failed outright. The index
            # is built from the registry rows and `english`, neither of which
            # compiles any SQL (dimagi-internal/connect-labs#1743).
            return {
                "registry_id": record.id,
                "version": record.version,
                "indicators": _indicator_index(record),
                "detail": (
                    "An index. Call again with `indicators` (ids or measure names) for the full "
                    "chain and the compiled statement."
                ),
            }

        out = []
        for ind in indicators:
            try:
                out.append(
                    explain(
                        record.properties_doc,
                        record.indicators_doc,
                        ind,
                        scope=scope,
                        llo_map=facts.get("llo_map") or None,
                        settings=facts.get("settings") or None,
                    )
                )
            except UnknownIndicator:
                raise MCPToolError("NOT_FOUND", f"No indicator or measure named {ind!r} in registry {registry_id}")

        # The compiled statement is the whole scope's SELECT: identical for every
        # indicator asked for. Return it once beside them rather than once per
        # indicator, which is what made asking for several expensive.
        compiled_sql = out[0].pop("compiled_sql", None) if out else None
        layer1 = out[0].pop("layer1", None) if out else None
        for explanation in out[1:]:
            explanation.pop("compiled_sql", None)
            explanation.pop("layer1", None)
        return {
            "registry_id": record.id,
            "version": record.version,
            "scope": scope,
            "indicators": out,
            "compiled_sql": compiled_sql,
            "layer1": layer1,
        }
    finally:
        access.close()
