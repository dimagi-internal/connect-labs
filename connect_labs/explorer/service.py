"""The explorer's two operations, shared by the MCP tools and the page.

``describe`` tells a person or an agent what they can query and how to answer
from it; ``query`` runs SQL. Both start at ``scope.resolve``, so neither surface
can forget the access check, and every query is written to the audit trail with
the opportunities it read and a hash of the SQL it ran.
"""

from __future__ import annotations

import hashlib
import logging

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.audit_trail.service import record
from connect_labs.labs.access.scopes import Caller

from . import engine, fields, guidance, scope
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "explorer_query"


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


def describe(
    caller: Caller,
    opportunity_ids,
    *,
    field_search: str | None = None,
    load_missing: bool = False,
    include_registries: bool = True,
) -> dict:
    opps = scope.resolve(caller, opportunity_ids)
    loaded = warm(caller, opps) if load_missing else []
    described = fields.search_fields(fields.describe_fields(opps), field_search)
    return {
        "opportunities": fields.cache_status(opps),
        "loaded": loaded,
        "relations": {
            "visits": {
                "grain": "one row per Connect visit",
                "columns": [{"name": n, "type": t, "means": m} for n, t, m in engine.VISIT_COLUMNS],
            }
        },
        "fields": described["fields"],
        "sampled_visits": described["sampled_visits"],
        "field_search": field_search,
        "registries": related_registries(caller, opps) if include_registries else [],
        "answering_rules": guidance.ANSWERING_RULES,
        "sql_notes": guidance.SQL_NOTES,
        "max_rows": {"default": DEFAULT_MAX_ROWS, "max": HARD_MAX_ROWS},
    }


def query(caller: Caller, opportunity_ids, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> dict:
    opps = scope.resolve(caller, opportunity_ids)
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
    return {**result.as_dict(), "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo} for o in opps]}
