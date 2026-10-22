"""Labs data's operations, shared by the MCP tools, the page and the labsdata agent.

``describe`` tells a person or an agent what they can query and how to answer
from it; ``query`` runs SQL; ``visits`` reads whole visits down to their form
JSON; ``workflow_context`` explains how a workflow turns those visits into what
it shows. Each starts at ``scope.resolve``, so no surface can forget the access
check, and every read is written to the audit trail with the opportunities it
read (and, for SQL, a hash of the statement).

**Who sees real rows.** A person (the page, their own MCP sign-in) sees everything
they hold. An agent calling through canopy as a visitor (``for_agent``) sees real
rows only when labs has cleared it for real data (``labs/canopy_panel.py``:
``real_data_clearance`` -- a PHI-declared agent, on ZDR runners, vouched for by
canopy on the call). Until then it gets structure, validation and synthetic data,
and hands SQL to the page, where the person runs it.
"""

from __future__ import annotations

import hashlib
import logging

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.audit_trail.service import record
from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.synthetic.provenance import all_generated

from . import engine, fields, guidance, scope
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "explorer_query"
VISITS_RESOURCE_TYPE = "labsdata_visits"


def _token(caller: Caller) -> str | None:
    if caller.access_token:
        return caller.access_token
    if caller.request is not None:
        oauth = getattr(caller.request, "session", {}).get("labs_oauth") or {}
        if oauth.get("access_token"):
            return oauth["access_token"]
    if caller.user is not None:
        from connect_labs.labs.connect_tokens import get_valid_access_token

        try:
            return get_valid_access_token(caller.user)
        except Exception:  # noqa: BLE001
            return None
    return None


def warm(caller: Caller, opps: list[scope.Opportunity]) -> list[dict]:
    """Load any opportunity that has nothing cached, as the caller. Reports, never raises."""
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    token = _token(caller)
    report = []
    pending = [s for s in fields.cache_status(opps) if not s["cached_visits"]]
    if not pending:
        return report
    if not token:
        return [{"opportunity_id": s["opportunity_id"], "error": "no Connect token to load with"} for s in pending]
    analysis = AnalysisPipeline(access_token=token)
    for s in pending:
        oid = s["opportunity_id"]
        try:
            rows = analysis.backend.fetch_raw_visits(
                opportunity_id=oid,
                access_token=token,
                expected_visit_count=analysis.expected_visits_for(oid),
                skip_form_json=True,
                user=caller.user,
            )
            report.append({"opportunity_id": oid, "loaded_visits": len(rows or [])})
        except Exception as e:  # noqa: BLE001 -- one opportunity must not cost the rest
            logger.warning("explorer warm failed for opp %s", oid, exc_info=True)
            report.append({"opportunity_id": oid, "error": str(e)[:200]})
    return report


def related_registries(caller: Caller, opps: list[scope.Opportunity]) -> list[dict]:
    """Semantic registries whose deployment names one of these opportunities."""
    token = _token(caller)
    if not token:
        return []
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    ids = {str(o.id) for o in opps}
    out = []
    try:
        access = SemanticRegistryDataAccess(access_token=token)
        try:
            for r in access.list_registries(include_shared=True):
                llo_map = (r.deployment or {}).get("llo_map") or {}
                covered = sorted(int(k) for k in llo_map if k in ids)
                if covered:
                    out.append({"registry_id": r.id, "name": r.name, "covers_opportunities": covered})
        finally:
            access.close()
    except Exception:  # noqa: BLE001 -- the registry list is a pointer, not the answer
        logger.info("explorer: registry lookup failed", exc_info=True)
    return out


class RealDataRefused(Exception):
    """An agent asked for real rows it is not cleared for. It may write SQL; the person runs it."""

    code = "real_data_stays_in_labs"
    message = (
        "Real visit data reaches an agent only when Labs has cleared it for real data, and this one is not. "
        "Validate the SQL with data_validate, try it on a synthetic opportunity, then give the person the "
        "SQL to run on the Data page (/labs/data/) -- they see the result there."
    )


def agent_sees_real_rows(for_agent: bool, opps) -> bool:
    """Whether this call may return real rows: always for a person; for an agent only when cleared."""
    if not for_agent or all_generated([o.id for o in opps]):
        return True
    from connect_labs.labs import canopy_panel

    return canopy_panel.real_data_clearance().granted


#: What an agent receives from one call, at most. A visit's form_json alone can run to
#: tens of kilobytes; past these the result is cut (and says so) rather than flooding
#: the model's context. A person on the page gets the result whole.
AGENT_CELL_CHARS = 4000
AGENT_RESULT_CHARS = 200_000


def shape_for_agent(result: dict) -> dict:
    """Cap long cells and the whole result, with notes written for the model (Scout's rule)."""
    import json

    rows, cut_cells, size = [], 0, 0
    for row in result.get("rows") or []:
        shaped = []
        for cell in row:
            text = (
                cell
                if isinstance(cell, str)
                else json.dumps(cell, default=str) if isinstance(cell, dict | list) else None
            )
            if text is not None and len(text) > AGENT_CELL_CHARS:
                cell = text[:AGENT_CELL_CHARS] + f"... [cut: {len(text)} chars]"
                cut_cells += 1
            shaped.append(cell)
        size += len(json.dumps(shaped, default=str))
        if size > AGENT_RESULT_CHARS:
            break
        rows.append(shaped)
    notes = list(result.get("notes") or [])
    if result.get("truncated"):
        notes.append(
            "These rows hit the row cap: do not present them as complete. To answer from all of the data, "
            "aggregate further (GROUP BY, COUNT, percentiles) or filter, then query again."
        )
    if len(rows) < len(result.get("rows") or []):
        notes.append(
            f"Only the first {len(rows)} of {result['row_count']} rows fit in your context. Select fewer "
            "columns (form_json is large) or aggregate."
        )
    if cut_cells:
        notes.append(
            f"{cut_cells} long cell(s) were cut at {AGENT_CELL_CHARS} characters. Read whole visits, form JSON "
            "and all, with data_visits; or select the specific fields with form_json #>> '{form,...}'."
        )
    return {**result, "rows": rows, "rows_returned": len(rows), "notes": notes}


#: Field-index keys computed from real answers. An agent gets structure only.
_DATA_DERIVED_FIELD_KEYS = ("values", "filled_pct", "distinct_in_sample")


def _structure_only(field: dict) -> dict:
    return {k: v for k, v in field.items() if k not in _DATA_DERIVED_FIELD_KEYS}


def describe(
    caller: Caller,
    opportunity_ids,
    *,
    field_search: str | None = None,
    load_missing: bool = False,
    include_registries: bool = True,
    for_agent: bool = False,
) -> dict:
    """What can be queried, and how to answer from it.

    ``for_agent`` (a canopy call): STRUCTURE only -- form paths, types, the SQL that
    reads each, the registries -- with no answer values, fill rates or row counts,
    unless every opportunity is synthetic or labs has cleared the agent for real data.
    The person on the page gets everything.
    """
    opps = scope.resolve(caller, opportunity_ids)
    real = not agent_sees_real_rows(for_agent, opps)
    loaded = warm(caller, opps) if load_missing and not real else []
    described = fields.search_fields(fields.describe_fields(opps), field_search)
    status = fields.cache_status(opps)
    if real:
        described["fields"] = [_structure_only(f) for f in described["fields"]]
        status = [
            {"opportunity_id": s["opportunity_id"], "opportunity_name": s["opportunity_name"], "llo": s["llo"]}
            for s in status
        ]
    return {
        "opportunities": status,
        "real_data_visible_to_you": not real,
        "loaded": loaded,
        "relations": {
            "visits": {
                "grain": "one row per Connect visit",
                "columns": [{"name": n, "type": t, "means": m} for n, t, m in engine.VISIT_COLUMNS],
            }
        },
        "fields": described["fields"],
        "sampled_visits": None if real else described["sampled_visits"],
        "field_search": field_search,
        "registries": related_registries(caller, opps) if include_registries else [],
        "answering_rules": guidance.AGENT_RULES if real else guidance.ANSWERING_RULES,
        "verifying_a_workflow": guidance.VERIFYING_A_WORKFLOW,
        "sql_notes": guidance.SQL_NOTES,
        "max_rows": {"default": DEFAULT_MAX_ROWS, "max": HARD_MAX_ROWS},
    }


def validate(sql: str) -> dict:
    """Check SQL end to end over zero rows. Touches no data, so it needs no scope."""
    return engine.validate_query(sql)


def query(
    caller: Caller, opportunity_ids, sql: str, max_rows: int = DEFAULT_MAX_ROWS, *, for_agent: bool = False
) -> dict:
    """Run SQL. ``for_agent`` (a canopy call) gets real rows only when cleared
    (``agent_sees_real_rows``), and its result is shaped for a model's context."""
    opps = scope.resolve(caller, opportunity_ids)
    if not agent_sees_real_rows(for_agent, opps):
        # Still tell the agent whether its SQL would run: that is what it needs to hand
        # the query to the person, and it reads no data.
        engine.validate_query(sql)
        raise RealDataRefused()
    # Never the SQL itself: the audit log carries no PHI content (docs/AUDIT_LOGGING.md),
    # and a query can hold a name or an answer as a literal. The hash still ties an
    # event to a query the caller can produce.
    text = sql or ""
    meta = {
        "opportunity_ids": [o.id for o in opps],
        "sql_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sql_length": len(text),
    }
    try:
        result = engine.run_query(opps, sql, max_rows=max_rows)
    except engine.QueryError as e:
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=opps[0].id,
            outcome=Outcome.FAILURE,
            metadata={**meta, "error": e.code},
            user=caller.user,
        )
        raise
    for o in opps:
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=o.id,
            record_count=result.row_count,
            metadata=meta,
            user=caller.user,
        )
    out = {
        **result.as_dict(),
        "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo} for o in opps],
        "open_on_page": open_on_page_url([o.id for o in opps], sql),
    }
    return shape_for_agent(out) if for_agent else out


def open_on_page_url(opportunity_ids, sql: str = "") -> str:
    """The Data page with these opportunities selected and this SQL in the editor (not run)."""
    from urllib.parse import urlencode

    from django.conf import settings
    from django.urls import reverse

    params = {"opps": ",".join(str(int(o)) for o in opportunity_ids or [])}
    if sql:
        params["sql"] = sql
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}{reverse('labsdata:index')}?{urlencode(params)}"


def visits(caller: Caller, opportunity_ids, visit_ids, *, for_agent: bool = False) -> dict:
    """Whole visits by id -- every column and the full form_json -- from the caller's opportunities."""
    opps = scope.resolve(caller, opportunity_ids)
    if not agent_sees_real_rows(for_agent, opps):
        raise RealDataRefused()
    found = engine.fetch_visits(opps, visit_ids)
    missing = sorted({str(v) for v in visit_ids} - {str(r["visit_id"]) for r in found})
    by_opp: dict[int, int] = {}
    for r in found:
        by_opp[r["opportunity_id"]] = by_opp.get(r["opportunity_id"], 0) + 1
    for oid, count in by_opp.items():
        record(
            Action.READ,
            resource_type=VISITS_RESOURCE_TYPE,
            opportunity_id=oid,
            record_count=count,
            metadata={"visits": count},
            user=caller.user,
        )
    return {
        "visits": found,
        "not_found": missing,
        "notes": (
            [
                "Not found means not in these opportunities' cached visits: check the visit_id and the "
                "opportunity, or whether the cache is fresh (data_describe shows cached_at)."
            ]
            if missing
            else []
        ),
    }
