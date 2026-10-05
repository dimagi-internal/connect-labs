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

import copy
import logging

from connect_labs.semantic.explain import UnknownIndicator, english, explain
from connect_labs.semantic.runtime import normalise_deployment_facts
from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import RegistryInvalid, validate_registry
from connect_labs.workflow.data_access import (
    RegistryBindingsUnknown,
    SemanticRegistryDataAccess,
    find_registry_bindings,
)

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
        "Create a semantic registry. Pass `seed_from` ('kmc', 'visit_quality') to copy one of the "
        "built-in on-disk registries as the starting point, or supply the documents "
        "directly. Requires a scope -- one of organization_id, program_id or opportunity_id -- "
        "which is where the registry lives and the scope to read, update or delete it through. "
        "Refuses anything that does not validate."
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
    is_write=True,
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
    if opportunity_id is None and program_id is None and organization_id is None:
        # A record created with no scope is readable by nobody: Connect answers a
        # read with no scope from the PUBLIC records only, and every scoped read
        # filters on a scope this record does not have. It used to be minted anyway
        # and its id handed back -- registry 25526 (#2233).
        raise MCPToolError(
            "INVALID_SCHEMA",
            "name the scope the registry lives in: pass one of organization_id, program_id or "
            "opportunity_id. A registry created with no scope cannot be read back, listed or deleted.",
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


def _bindings_scopes(user, token, home: dict) -> list[dict]:
    """Every scope a workflow binding this registry could be read from by this caller.

    The registry's own scope, the public (shared) workflows, and every organisation,
    programme and opportunity the caller holds. A workflow in a scope the caller
    does not hold cannot be seen, so it cannot be counted.
    """
    from connect_labs.labs.access.scopes import Caller, ScopesUnavailable, holdings

    try:
        held = holdings(Caller(user=user, access_token=token))
    except ScopesUnavailable as exc:
        raise MCPToolError(
            "UPSTREAM_UNAVAILABLE",
            f"cannot check which workflows bind this registry ({exc}), so it was not deleted.",
        ) from exc
    scopes = [dict(home), {}]
    for org in held.org_slugs:
        if str(org).isdigit():
            scopes.append({"organization_id": int(org)})
    scopes += [{"program_id": p} for p in sorted(held.program_ids)]
    scopes += [{"opportunity_id": o} for o in sorted(held.opportunity_ids)]
    unique, seen = [], set()
    for scope in scopes:
        key = tuple(sorted(scope.items()))
        if key not in seen:
            seen.add(key)
            unique.append(scope)
    return unique


@register(
    name="semantic_registry_delete",
    description=(
        "Delete a semantic registry. IRREVERSIBLE. Name the scope it lives in: exactly one of "
        "organization_id, program_id or opportunity_id (the scope it was created with). Needs "
        "write access to that scope -- the same access semantic_registry_update needs. Refuses "
        "while any workflow binds the registry through its registry_source, and names those "
        "workflows: rebind or delete them first. The check covers every scope you can read; a "
        "workflow in a scope you do not hold cannot be seen."
    ),
    input_schema={
        "type": "object",
        "properties": {"registry_id": {"type": "integer"}, **_SCOPE_PROPS},
        "required": ["registry_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def semantic_registry_delete(user, registry_id: int, opportunity_id=None, program_id=None, organization_id=None):
    home = {
        k: v
        for k, v in (
            ("organization_id", organization_id),
            ("program_id", program_id),
            ("opportunity_id", opportunity_id),
        )
        if v is not None
    }
    if len(home) != 1:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "name the one scope the registry lives in: exactly one of organization_id, program_id "
            f"or opportunity_id (got {sorted(home) or 'none'}).",
        )

    access = _access(user, **home)
    try:
        # An exact-scope read: Connect 404s a scoped read for anyone who is not a
        # member of that scope, so finding it HERE is the write-access check. A
        # shared registry read from outside its scope does not count.
        record = access.get_registry_in_scope(registry_id)
        if record is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"No semantic registry {registry_id} in {_scope_label(home)} that you can write. "
                "Name the scope it was created in.",
            )

        try:
            bindings = find_registry_bindings(
                access.access_token, registry_id, _bindings_scopes(user, access.access_token, home)
            )
        except RegistryBindingsUnknown as exc:
            raise MCPToolError(
                "UPSTREAM_UNAVAILABLE",
                f"cannot check which workflows bind registry {registry_id}: {exc}. Not deleted.",
            ) from exc
        if bindings:
            ids = ", ".join(str(b["workflow_id"]) for b in bindings)
            raise MCPToolError(
                "CONFLICT",
                f"registry {registry_id} is bound by workflow(s) {ids}; deleting it would break them. "
                "Rebind them (workflow_update_definition registry_source) or delete them first. Not deleted.",
                details={"workflows": bindings},
            )

        access.delete_registry(registry_id)
        # Connect's DELETE answers 200 even when it skipped an id the caller could
        # not delete, so "no error" is not "deleted": read it back.
        if access.get_registry_in_scope(registry_id) is not None:
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"Connect did not delete registry {registry_id} in {_scope_label(home)}; it is still there.",
            )
    finally:
        access.close()
    return {"registry_id": registry_id, "name": record.name, "deleted": True, "scope": home}


def _scope_label(scope: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in scope.items())


#: Item-level edits, by the document and list each one lands in. A registry is
#: three lists of named items -- properties and aggregates in the properties
#: document, measures in the indicators document -- so a small edit names the
#: items it touches rather than resending the document they live in (#2224).
_ITEM_LISTS = {
    "properties": ("properties_doc", "properties"),
    "aggregates": ("properties_doc", "aggregates"),
    "measures": ("indicators_doc", "measures"),
}

_ITEM_ARRAY = {"type": "array", "items": {"type": "object"}}


def _apply_item_ops(items: list, kind: str, upserts: list | None, removals: list | None, insert_after: str | None):
    """Return a NEW list with `removals` dropped and `upserts` merged in, by `name`.

    An upsert whose name exists REPLACES that item where it stands, so a
    replacement never moves a measure in the scorecard. A new one is appended,
    or -- with `insert_after` -- placed after that item, the new items keeping
    the order they were given in. Every name is checked before anything is
    applied: a typo in a removal or an anchor is refused rather than ignored,
    since "removed nothing" and "inserted at the end" would both report success.
    """
    upserts = list(upserts or [])
    removals = list(removals or [])
    existing = [i.get("name") if isinstance(i, dict) else None for i in items]

    seen = set()
    for n, item in enumerate(upserts):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise MCPToolError("INVALID_SCHEMA", f"upsert_{kind}[{n}] must be an object with a non-empty `name`")
        if item["name"] in seen:
            raise MCPToolError("INVALID_SCHEMA", f"upsert_{kind} names {item['name']!r} more than once")
        seen.add(item["name"])

    for name in removals:
        if not isinstance(name, str):
            raise MCPToolError("INVALID_SCHEMA", f"remove.{kind} must be a list of names")
    unknown = sorted(set(removals) - set(existing))
    if unknown:
        raise MCPToolError("NOT_FOUND", f"remove.{kind}: the registry defines no {kind} named {', '.join(unknown)}")
    both = sorted(set(removals) & seen)
    if both:
        raise MCPToolError("INVALID_SCHEMA", f"{kind} {', '.join(both)} is both upserted and removed; pick one")

    if insert_after is not None and (insert_after not in existing or insert_after in removals):
        raise MCPToolError(
            "NOT_FOUND",
            f"insert_after: the registry has no {kind} named {insert_after!r} to insert after"
            + (" (it is being removed)" if insert_after in removals else ""),
        )

    by_name = {item["name"]: copy.deepcopy(item) for item in upserts}
    merged = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else None
        if name in removals:
            continue
        merged.append(by_name.pop(name) if name in by_name else copy.deepcopy(item))
    new = [by_name[item["name"]] for item in upserts if item["name"] in by_name]
    if insert_after is None:
        merged.extend(new)
    else:
        at = next(i for i, item in enumerate(merged) if isinstance(item, dict) and item.get("name") == insert_after)
        merged[at + 1 : at + 1] = new
    return merged


def _insert_after_for(insert_after, kind: str):
    """`insert_after` is a measure name, or {properties|measures|aggregates: name}."""
    if insert_after is None:
        return None
    if isinstance(insert_after, str):
        return insert_after if kind == "measures" else None
    if isinstance(insert_after, dict):
        unknown = sorted(set(insert_after) - set(_ITEM_LISTS))
        if unknown:
            raise MCPToolError("INVALID_SCHEMA", f"insert_after: unknown list(s) {', '.join(unknown)}")
        return insert_after.get(kind)
    raise MCPToolError(
        "INVALID_SCHEMA", "insert_after must be a measure name or {measures|properties|aggregates: name}"
    )


@register(
    name="semantic_registry_update",
    description=(
        "Patch a semantic registry. Two ways to change the definition, and the small one is "
        "usually the right one:\n"
        "- ITEM-LEVEL (prefer this): `upsert_measures` / `upsert_properties` / `upsert_aggregates` "
        "are lists of items keyed by `name` -- an existing name is REPLACED where it stands, a new "
        "one is appended, or placed after `insert_after` (a measure name, or "
        "{measures|properties|aggregates: name}) so a new indicator lands beside related ones in "
        "the scorecard. `remove` is {properties: [names], measures: [names], aggregates: [names]}. "
        "Adding one indicator with its numerator and denominator is three small items, not the "
        "whole document. An unknown name in `remove` or `insert_after` is refused.\n"
        "- WHOLE-DOCUMENT: `properties_doc` / `indicators_doc` / `deployment` replace that "
        "document outright. Do not combine a whole document with item edits to the same document.\n"
        "Any document you omit is left alone. Validation runs against the MERGED result, so a "
        "change cannot silently break another part of the registry, and a refused edit saves "
        "nothing. A definition change bumps the version. Pass `expected_version` (from "
        "semantic_registry_get/list) to refuse the write if someone else has changed the "
        "registry since you read it. For one indicator's display keys, "
        "semantic_registry_set_indicator_meta is smaller still."
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
            "upsert_properties": {
                **_ITEM_ARRAY,
                "description": "Layer-2 properties, keyed by `name`: replace in place, else append.",
            },
            "upsert_measures": {
                **_ITEM_ARRAY,
                "description": "Indicator measures, keyed by `name`: replace in place, else append "
                "(or insert after `insert_after`).",
            },
            "upsert_aggregates": {
                **_ITEM_ARRAY,
                "description": "Per-entity aggregates, keyed by `name`: replace in place, else append.",
            },
            "remove": {
                "type": "object",
                "properties": {k: {"type": "array", "items": {"type": "string"}} for k in _ITEM_LISTS},
                "additionalProperties": False,
                "description": "Names to delete, e.g. {measures: ['old_rate', 'old_rate_numerator']}.",
            },
            "insert_after": {
                "type": ["string", "object"],
                "description": "Where NEW upserted items go: a measure name, or "
                "{measures|properties|aggregates: name}. Replacements keep their position.",
            },
            "expected_version": {
                "type": "integer",
                "description": "Refuse the write unless the registry is still at this version.",
            },
            **_SCOPE_PROPS,
        },
        "required": ["registry_id"],
        "additionalProperties": False,
    },
    is_write=True,
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
    upsert_properties=None,
    upsert_measures=None,
    upsert_aggregates=None,
    remove=None,
    insert_after=None,
    expected_version=None,
    opportunity_id=None,
    program_id=None,
    organization_id=None,
):
    upserts = {"properties": upsert_properties, "measures": upsert_measures, "aggregates": upsert_aggregates}
    remove = remove or {}
    item_kinds = [k for k in _ITEM_LISTS if upserts[k] or remove.get(k)]
    whole = {"properties_doc": properties_doc, "indicators_doc": indicators_doc}
    clash = sorted({_ITEM_LISTS[k][0] for k in item_kinds if whole[_ITEM_LISTS[k][0]] is not None})
    if clash:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"{', '.join(clash)} was sent whole AND edited item by item; send one or the other.",
        )
    if insert_after is not None:
        anchored = ["measures"] if isinstance(insert_after, str) else list(insert_after or {})
        unanchored = [k for k in anchored if k in upserts and not upserts[k]]
        if not anchored or unanchored:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "insert_after places NEW upserted items, but nothing is upserted into "
                f"{', '.join(unanchored or ['any list'])}; send upsert_{(unanchored or ['measures'])[0]} "
                "or drop insert_after.",
            )

    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        before = access.get_registry(registry_id)
        if before is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry with id {registry_id}")
        if expected_version is not None and before.version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"registry {registry_id} is at version {before.version}, not the expected "
                f"{expected_version}: it changed since you read it. Re-read it with "
                "semantic_registry_get and reapply your edit.",
            )

        edited = {}
        changes = {}
        for kind in item_kinds:
            doc_key, list_key = _ITEM_LISTS[kind]
            if doc_key not in edited:
                source = before.properties_doc if doc_key == "properties_doc" else before.indicators_doc
                edited[doc_key] = copy.deepcopy(source or {})
            doc = edited[doc_key]
            doc[list_key] = _apply_item_ops(
                doc.get(list_key) or [],
                kind,
                upserts[kind],
                remove.get(kind),
                _insert_after_for(insert_after, kind),
            )
            changes[kind] = {
                "upserted": [i["name"] for i in upserts[kind] or []],
                "removed": list(remove.get(kind) or []),
            }

        record = access.update_registry(
            registry_id,
            name=name,
            description=description,
            properties=edited.get("properties_doc", properties_doc),
            indicators=edited.get("indicators_doc", indicators_doc),
            deployment=deployment,
            is_shared=is_shared,
        )
    except RegistryInvalid as exc:
        raise MCPToolError("INVALID_SCHEMA", "registry rejected:\n  " + "\n  ".join(exc.errors)) from exc
    finally:
        access.close()
    out = {**_summary(record), "_version_before": before.version, "_version_after": record.version}
    if changes:
        out["items_changed"] = changes
    return out


@register(
    name="semantic_registry_set_indicator_meta",
    description=(
        "Set one or more `meta` keys on named indicators, leaving every other measure, every "
        "other key and the other two documents exactly as they are. "
        "For a single meta key this is the smallest write there is: even an item-level "
        "`semantic_registry_update` resends the whole measure. "
        "Validation still runs against the MERGED registry, so a patch that breaks an indicator "
        "is refused, and a definition change still bumps the version. "
        "`patches` is {indicator_id: {meta_key: value}}; a null value REMOVES the key. Refuses "
        "an indicator the registry does not define, rather than silently creating one -- a typo "
        "in an indicator id would otherwise write a meta block nothing reads."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "registry_id": {"type": "integer"},
            "patches": {
                "type": "object",
                "description": 'e.g. {"mean_early_growth_rate": {"benchmarkable": true}}',
            },
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            "organization_id": {"type": "integer"},
        },
        "required": ["registry_id", "patches"],
        "additionalProperties": False,
    },
    is_write=True,
)
def semantic_registry_set_indicator_meta(
    user,
    registry_id: int,
    patches: dict,
    opportunity_id=None,
    program_id=None,
    organization_id=None,
):
    access = _access(user, opportunity_id, program_id, organization_id)
    try:
        before = access.get_registry(registry_id)
        if before is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry with id {registry_id}")

        doc = copy.deepcopy(before.indicators_doc)
        measures = doc.get("measures") or []
        by_indicator = {}
        for measure in measures:
            indicator = ((measure.get("meta") or {}).get("indicator") or "").strip()
            if indicator:
                by_indicator[indicator] = measure

        unknown = sorted(set(patches) - set(by_indicator))
        if unknown:
            raise MCPToolError(
                "NOT_FOUND",
                f"registry {registry_id} defines no indicator(s): {', '.join(unknown)}. "
                f"It defines: {', '.join(sorted(by_indicator))}",
            )

        applied = {}
        for indicator, keys in patches.items():
            if not isinstance(keys, dict):
                raise MCPToolError("INVALID_SCHEMA", f"patches[{indicator!r}] must be an object of meta keys")
            meta = by_indicator[indicator].setdefault("meta", {})
            for key, value in keys.items():
                if value is None:
                    meta.pop(key, None)
                else:
                    meta[key] = value
            applied[indicator] = dict(keys)

        record = access.update_registry(registry_id, indicators=doc)
    except RegistryInvalid as exc:
        raise MCPToolError("INVALID_SCHEMA", "registry rejected:\n  " + "\n  ".join(exc.errors)) from exc
    finally:
        access.close()
    return {
        **_summary(record),
        "applied": applied,
        "_version_before": before.version,
        "_version_after": record.version,
    }


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
        "The exact logic behind an indicator, read from the registry with nothing hidden: the"
        " compiled measure expression, its numerator/denominator components, the Layer-2 "
        "property chain it depends on in evaluation order with every constant substituted, "
        "the per-entity aggregates and (when the registry declares a reading series) the "
        "series window derivations it touches, and the constants used. The full compiled "
        "statement for the scope comes back ONCE, beside the indicators (Layer 1, the "
        "pipeline rows, is a named placeholder -- read that schema with pipeline_get). Pass "
        "an indicator id (mortality, pct_impossible_weight_changes, Q02), a measure name "
        "(q02), or several. This is how a second engine reproduces a number instead of "
        "trusting its label. Omit `indicators` "
        "for an INDEX of every top-level indicator -- id, title and one-line definition -- "
        "then ask again by id for the ones you want; the "
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
                    "Indicator ids or measure names, e.g. ['mortality', 'pct_impossible_weight_changes']. "
                    "Omit for an index of every top-level indicator (id, title, one-line definition) "
                    "rather than their full chains."
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
