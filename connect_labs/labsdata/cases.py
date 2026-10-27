"""Case-level fields in a de-identified export: a registry's properties, one row per case.

A workflow space's registry computes one row per case (a baby, a mother) -- the
``props`` relation its indicators are graded from (``semantic.compiler.compile_props_sql``).
Its aggregates and properties can go out beside the visit rows, as ``cases.csv``, keyed
by the same case codes, so an evaluator gets "KMC hours per baby" without recomputing it.

Each one is a dictionary entry (key ``@case:<name>``, level ``case``) whose setting may
be STRICTER than what it inherits from the form fields it is computed from
(``lineage``), never looser: a property computed from a field nobody reviewed inherits
``unset`` and can only be dropped. Entries are added by a person (``refresh``), so
the list follows the registry without anyone writing it by hand.

The SQL that computes them is compiled when the export is asked for, as the person,
from the workflow, its pipelines and its registry -- the build then runs exactly that
statement, over exactly the opportunities they held.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from . import dictionary as dictionary_mod
from . import lineage, spaces
from .models import DictionaryChange, DictionaryEntry, DictionaryFlag

PREFIX = dictionary_mod.CASE_PREFIX
#: What a case property's kind is in the dictionary: computed, so judged by its sources.
DERIVED = "derived"
floor = dictionary_mod.case_floor
allowed = dictionary_mod.case_allowed


def is_case_entry(entry) -> bool:
    key = entry["key"] if isinstance(entry, dict) else entry.key
    return key.startswith(PREFIX)


def _quasi(node: dict) -> bool:
    return any(w in node["name"] for w in ("date", "dob", "birth"))


@transaction.atomic
def refresh(caller, opened: spaces.OpenSpace, *, user) -> dict:
    """Add a case field for every aggregate and property of the space's registry, and
    bring each one's inherited setting up to date. Anyone who can open the space: it
    adds unreviewed (dropped) fields and changes no setting a person made -- except
    that a field whose sources became stricter than its setting goes back to unreviewed."""
    out = lineage.for_space(caller, opened)
    if not out.get("available"):
        return out
    dictionary = dictionary_mod.for_space(opened)
    existing = {e.key: e for e in dictionary.entries.filter(key__startswith=PREFIX)}
    added = updated = 0
    nodes = [n for n in out["columns"] if n["layer"] == "aggregate"] + out["properties"]
    for node in nodes:
        key = PREFIX + node["name"]
        inherits = node["inherits"]
        suggested = floor(inherits)
        entry = existing.get(key)
        if entry is None:
            DictionaryEntry.objects.create(
                dictionary=dictionary,
                key=key,
                label=f"case.{node['name']}",
                kind=DERIVED,
                meaning=node.get("label") or "",
                level=DictionaryEntry.CASE,
                setting=suggested,
                suggested_setting=suggested,
                inherits=inherits,
                quasi=_quasi(node),
            )
            added += 1
            continue
        if entry.inherits == inherits:
            continue
        entry.inherits, entry.suggested_setting = inherits, suggested
        fields = ["inherits", "suggested_setting"]
        if entry.reviewed and not allowed(entry.setting, inherits):
            # Its sources are stricter now than its setting allows: dropped until reviewed.
            entry.reviewed_by = entry.reviewed_at = None
            fields += ["reviewed_by", "reviewed_at"]
            DictionaryFlag.objects.create(
                dictionary=dictionary,
                entry=entry,
                kind=DictionaryFlag.KIND_CHANGED,
                detail={"paths": [entry.label], "was": entry.setting, "now": f"inherits {inherits}"},
            )
        entry.save(update_fields=fields)
        updated += 1
    DictionaryChange.objects.create(
        dictionary=dictionary, action="case_fields", after={"added": added, "updated": updated}, user=user
    )
    return {**out, "case_fields": {"added": added, "updated": updated}}


def compile_sql(caller, opened: spaces.OpenSpace, *, as_of: dt.date | None) -> dict:
    """The statement that computes every case row of the space's workflow's registry over
    the opportunities the person holds, as of ``as_of`` -- compiled now, as them."""
    from connect_labs.semantic.compiler import compile_props_sql
    from connect_labs.semantic.layer1 import build_visit_sql
    from connect_labs.semantic.model import resolve_model
    from connect_labs.semantic.workflow_binding import build_evaluate_inputs, resolve_registry_for
    from connect_labs.workflow.data_access import PipelineDataAccess, SemanticRegistryDataAccess

    from .workflow_context import read_workflow

    workflow = dict(opened.space.workflow or {})
    if not workflow:
        raise dictionary_mod.DictionaryError(
            "no_workflow", "Case fields come from a workflow's registry: make this space from a workflow."
        )
    dictionary = dictionary_mod.for_space(opened)
    definition, token, owner, _opps = read_workflow(
        caller,
        int(workflow["id"]),
        opportunity_id=workflow.get("opportunity_id"),
        program_id=workflow.get("program_id"),
    )
    props_doc, registry, llo_map, _settings, _deployment, source = resolve_registry_for(
        definition,
        registry_access_factory=lambda: SemanticRegistryDataAccess(access_token=token, **owner),
        registry_id_override=dictionary.registry_id,
    )
    pipeline_config, extra_fields = build_evaluate_inputs(
        definition, lambda: PipelineDataAccess(access_token=token, **owner), props_doc=props_doc
    )
    visit_sql = build_visit_sql(pipeline_config, opened.selection.ids, extra_fields=extra_fields, props_doc=props_doc)
    sql, _compiled = compile_props_sql(
        props_doc,
        registry,
        visit_sql,
        as_of=f"DATE '{as_of.isoformat()}'" if as_of else "CURRENT_DATE",
        llo_map=llo_map,
    )
    return {
        "sql": sql,
        "row_id": resolve_model(props_doc).row_id,
        "registry": {k: v for k, v in source.items() if k in ("registry_id", "name")},
        "compiled_at": timezone.now().isoformat(),
    }


def case_key(row: dict, row_id: str) -> str:
    """The case's own id, from the row key ``<opportunity>|<case id>``."""
    value = str(row.get(row_id) or "")
    return value.split("|", 1)[1] if "|" in value else value


def rows(sql: str):
    """Run the compiled statement and return its rows as dicts. As the app (the semantic
    engine always reads the cache this way); its opportunities were fixed when it was
    compiled, as the person who asked."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(sql)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, r)) for r in cursor.fetchall()]
