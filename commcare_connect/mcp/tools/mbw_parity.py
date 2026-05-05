"""MBW v1↔v3 dashboard parity tools.

Three MCP tools that reuse the in-process dashboard builders to produce
v1 and v3 dashboard payloads against real production data:

- `mbw_dashboard_v3` — runs v3's pipeline path and assembles its
  dashboard payload via the same logic the JSX uses (mirrored in
  Python via `build_v3_dashboard_payload`).
- `mbw_dashboard_v1` — runs v1's MBW_GPS_PIPELINE_CONFIG, reads cached
  raw CCHQ forms (registration_forms + gs_forms) from the labs raw
  visit cache (populated by v3's cchq_forms pipelines), and assembles
  v1's payload via `build_v1_dashboard_payload`.
- `mbw_dashboard_diff` — runs both and walks DASHBOARD_CONTRACT,
  returning a structured per-leaf diff report.

These tools assume v3 has already run for the opportunity (so the cchq
form caches are populated). If they haven't, registration_forms and
gs_forms will be empty and v1's payload will only have visit-side
content.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from commcare_connect.labs.analysis.backends.sql.models import RawVisitCache
from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess
from commcare_connect.workflow.templates.mbw_monitoring.dashboard_builder import build_v1_dashboard_payload
from commcare_connect.workflow.tests.mbw_parity.diff import diff_payloads
from commcare_connect.workflow.tests.mbw_parity.payload_contract import DASHBOARD_CONTRACT
from commcare_connect.workflow.tests.mbw_parity.v3_python_port import build_v3_dashboard_payload

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


# ---- helpers ---------------------------------------------------------------


def _find_v3_workflow(wda: WorkflowDataAccess, opportunity_id: int) -> int | None:
    """Find the mbw_monitoring_v3 workflow for the given opportunity, if any."""
    for definition in wda.list_definitions():
        if definition.template_type == "mbw_monitoring_v3" and definition.opportunity_id == opportunity_id:
            return definition.id
    return None


def _pipeline_id_by_alias(wda: WorkflowDataAccess, workflow_id: int) -> dict[str, int]:
    """Resolve {pipeline_alias: pipeline_id} for a workflow's sources."""
    definition = wda.get_definition(workflow_id)
    if not definition:
        return {}
    return {src.get("alias"): src.get("pipeline_id") for src in (definition.pipeline_sources or [])}


def _read_cached_cchq_forms(opportunity_id: int, pipeline_id: int) -> list[dict]:
    """Read raw CCHQ form_json dicts from RawVisitCache for a pipeline.

    Returns the original CCHQ form-API shape (`{"id", "form": {...},
    "metadata": {...}, "received_on"}`) — the same shape v1's
    `extract_mother_metadata_from_forms` and the GS-score extractor
    consume directly.
    """
    if not pipeline_id:
        return []
    rows = RawVisitCache.objects.filter(opportunity_id=opportunity_id, pipeline_id=pipeline_id).only(
        "visit_id", "username", "form_json", "date_created"
    )
    forms: list[dict] = []
    for r in rows:
        fj = r.form_json or {}
        # form_json may be the bare form (`{"@name": ..., ...}`) or the
        # API wrapper (`{"id", "form": {...}, "metadata": ...}`). Normalize.
        if isinstance(fj, dict) and "form" in fj:
            forms.append(fj)
        else:
            forms.append(
                {
                    "id": r.visit_id,
                    "form": fj if isinstance(fj, dict) else {},
                    "metadata": {"username": r.username},
                    "received_on": r.date_created.isoformat() if r.date_created else "",
                }
            )
    return forms


def _v3_visits_pipeline_rows(pipelines: dict, alias: str) -> list[dict]:
    """Pull pipeline rows out of `WorkflowDataAccess.get_pipeline_data` output
    and normalize to the dict shape `_v3PipelineRows` produces in the JS.

    Each row carries `_username` / `_visit_date` / `_opportunity_id`
    plus the computed fields flat at the top level — matches what the
    Python port consumes.
    """
    p = pipelines.get(alias) or {}
    out: list[dict] = []
    for row in p.get("rows") or []:
        fields = row.get("computed") if isinstance(row, dict) else None
        if not isinstance(fields, dict):
            fields = row.get("custom_fields") if isinstance(row, dict) else None
        if not isinstance(fields, dict):
            fields = row if isinstance(row, dict) else {}
        normalized = dict(fields)
        normalized["_username"] = row.get("username") or fields.get("username") or ""
        normalized["_visit_date"] = row.get("visit_date") or fields.get("visit_date") or ""
        normalized["_opportunity_id"] = row.get("opportunity_id") or fields.get("opportunity_id") or None
        out.append(normalized)
    return out


def _run_v3(user, opportunity_id: int, workflow_id: int | None, current_date_str: str | None) -> tuple[int, dict]:
    """Returns (workflow_id, v3_dashboard_payload)."""
    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token)
    try:
        wf_id = workflow_id or _find_v3_workflow(wda, opportunity_id)
        if not wf_id:
            raise MCPToolError(
                "NOT_FOUND",
                f"No mbw_monitoring_v3 workflow found for opportunity {opportunity_id}.",
            )
        pipelines = wda.get_pipeline_data(wf_id, opportunity_id)
        # Workers / FLW names from the per-FLW visits pipeline.
        visits_rows = _v3_visits_pipeline_rows(pipelines, "visits")
        active_usernames = {r["_username"].lower() for r in visits_rows if r.get("_username")}
        flw_name_map = {u: u for u in active_usernames}

        payload = build_v3_dashboard_payload(
            visits_rows=visits_rows,
            visits_gps_rows=_v3_visits_pipeline_rows(pipelines, "visits_gps"),
            registrations_rows=_v3_visits_pipeline_rows(pipelines, "registrations"),
            gs_forms_rows=_v3_visits_pipeline_rows(pipelines, "gs_forms"),
            active_usernames=active_usernames,
            flw_name_map=flw_name_map,
            current_date_str=current_date_str,
        )
        return wf_id, payload
    finally:
        wda.close()


def _run_v1(user, opportunity_id: int, workflow_id: int | None, current_date_str: str | None) -> tuple[int, dict]:
    """Returns (workflow_id, v1_dashboard_payload).

    Reuses the v3 workflow's cchq_forms caches for registration_forms +
    gs_forms (no fresh CCHQ fetch). Builds v1's pipeline_rows from the
    visits-side ComputedVisitCache via WorkflowDataAccess too — uses v3's
    visits_gps pipeline output as the v1 pipeline rows surrogate.
    """
    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        wf_id = workflow_id or _find_v3_workflow(wda, opportunity_id)
        if not wf_id:
            raise MCPToolError("NOT_FOUND", f"No mbw_monitoring_v3 workflow for opp {opportunity_id}.")

        ids_by_alias = _pipeline_id_by_alias(wda, wf_id)
        registration_forms = _read_cached_cchq_forms(opportunity_id, ids_by_alias.get("registrations"))
        gs_forms = _read_cached_cchq_forms(opportunity_id, ids_by_alias.get("gs_forms"))

        # v1 pipeline_rows: synthesize from v3's visits_gps ComputedVisitCache,
        # which is visit-level and contains every field v1's helpers read.
        # We wrap each row in a stand-in object with the dotted attrs v1 expects.
        pipelines = wda.get_pipeline_data(wf_id, opportunity_id)
        v3_visit_rows = _v3_visits_pipeline_rows(pipelines, "visits_gps")

        class _V1Row:
            __slots__ = ("id", "username", "visit_date", "latitude", "longitude", "entity_name", "computed")

            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        pipeline_rows = []
        for r in v3_visit_rows:
            vd = r.get("_visit_date")
            if isinstance(vd, str) and vd:
                try:
                    vd = _date.fromisoformat(vd[:10])
                except ValueError:
                    vd = None
            pipeline_rows.append(
                _V1Row(
                    id=r.get("visit_id") or r.get("id"),
                    username=r.get("_username"),
                    visit_date=vd,
                    latitude=r.get("latitude"),
                    longitude=r.get("longitude"),
                    entity_name=r.get("entity_name") or "",
                    computed={k: v for k, v in r.items() if not k.startswith("_")},
                )
            )

        active_usernames = {r.username.lower() for r in pipeline_rows if r.username}
        flw_names = {u: u for u in active_usernames}

        current_date = _date.fromisoformat(current_date_str) if current_date_str else _date.today()

        payload = build_v1_dashboard_payload(
            pipeline_rows=pipeline_rows,
            registration_forms=registration_forms,
            gs_forms=gs_forms,
            active_usernames=active_usernames,
            flw_names=flw_names,
            current_date=current_date,
        )
        return wf_id, payload
    finally:
        wda.close()
        pda.close()


def _summarize_payload(payload: dict) -> dict[str, Any]:
    """Trim the dashboard payload to the bits worth showing in MCP output —
    full dashboards are large (per-mother drilldown across all FLWs)."""
    overview = payload.get("overview_data") or {}
    return {
        "active_usernames": payload.get("active_usernames"),
        "totals": {
            "total_visit_rows": overview.get("total_visit_rows"),
            "total_registration_forms": overview.get("total_registration_forms"),
            "total_gs_forms": overview.get("total_gs_forms"),
            "followup_total_cases": (payload.get("followup_data") or {}).get("total_cases"),
            "gps_total_visits": (payload.get("gps_data") or {}).get("total_visits"),
            "gps_total_flagged": (payload.get("gps_data") or {}).get("total_flagged"),
        },
        "overview_flw_summaries": overview.get("flw_summaries"),
        "performance_data": payload.get("performance_data"),
    }


# ---- MCP tools ------------------------------------------------------------


@register(
    name="mbw_dashboard_v3",
    description=(
        "Build the v3 MBW dashboard payload for an opportunity using the "
        "in-process v3 pipeline path. Returns a trimmed summary "
        "(per-FLW overview rows, performance buckets, totals)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "workflow_id": {
                "type": "integer",
                "description": "Optional: the mbw_monitoring_v3 workflow id. Auto-discovered if omitted.",
            },
            "current_date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD) for grace-period and last-active calcs.",
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def mbw_dashboard_v3(user, opportunity_id: int, workflow_id: int | None = None, current_date: str | None = None):
    wf_id, payload = _run_v3(user, opportunity_id, workflow_id, current_date)
    summary = _summarize_payload(payload)
    summary["workflow_id"] = wf_id
    return summary


@register(
    name="mbw_dashboard_v1",
    description=(
        "Build the v1 MBW dashboard payload for an opportunity by running "
        "v1's compute path against cached pipeline rows + cached CCHQ raw "
        "forms (populated by v3's cchq_forms pipelines). Returns the same "
        "trimmed summary shape as mbw_dashboard_v3 so the two are diffable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "workflow_id": {
                "type": "integer",
                "description": (
                    "Optional: the mbw_monitoring_v3 workflow whose caches "
                    "should source raw CCHQ forms. Auto-discovered if omitted."
                ),
            },
            "current_date": {"type": "string"},
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def mbw_dashboard_v1(user, opportunity_id: int, workflow_id: int | None = None, current_date: str | None = None):
    wf_id, payload = _run_v1(user, opportunity_id, workflow_id, current_date)
    summary = _summarize_payload(payload)
    summary["workflow_id"] = wf_id
    return summary


@register(
    name="mbw_dashboard_diff",
    description=(
        "Run both v1 and v3 dashboard builders for an opportunity and walk "
        "DASHBOARD_CONTRACT leaf-by-leaf to report deltas. Returns a "
        "ParityReport-shaped dict with diffs, missing-in-v1 / missing-in-v3 "
        "leaves, and per-FLW comparison summary."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "workflow_id": {"type": "integer"},
            "current_date": {"type": "string"},
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def mbw_dashboard_diff(user, opportunity_id: int, workflow_id: int | None = None, current_date: str | None = None):
    wf_id, v3_payload = _run_v3(user, opportunity_id, workflow_id, current_date)
    _, v1_payload = _run_v1(user, opportunity_id, wf_id, current_date)

    report = diff_payloads(v1_payload, v3_payload, DASHBOARD_CONTRACT)

    # Hand-pick high-value field-level checks beyond the contract walk —
    # leaves the contract doesn't yet cover but we care about for the
    # MBW Overview parity story.
    extra: list[dict] = []
    v1_overview = {s["username"]: s for s in (v1_payload.get("overview_data") or {}).get("flw_summaries") or []}
    v3_overview = {s["username"]: s for s in (v3_payload.get("overview_data") or {}).get("flw_summaries") or []}
    for username in sorted(set(v1_overview) | set(v3_overview)):
        v1_row = v1_overview.get(username, {})
        v3_row = v3_overview.get(username, {})
        for field in (
            "cases_registered",
            "eligible_mothers",
            "followup_rate",
            "ebf_pct",
            "first_gs_score",
        ):
            v1_val = v1_row.get(field)
            v3_val = v3_row.get(field)
            if v1_val != v3_val:
                extra.append(
                    {
                        "path": f"overview_data.flw_summaries[{username}].{field}",
                        "v1_value": v1_val,
                        "v3_value": v3_val,
                    }
                )

    return {
        "workflow_id": wf_id,
        "leaves_checked": report.leaves_checked,
        "diff_count": len(report.diffs),
        "diffs": [
            {
                "path": d.path,
                "v1_value": d.v1_value,
                "v3_value": d.v3_value,
                "delta": d.delta,
                "tolerance_kind": d.tolerance.kind,
                "reason": d.reason,
            }
            for d in report.diffs
        ],
        "missing_in_v1": list(report.missing_in_v1),
        "missing_in_v3": list(report.missing_in_v3),
        "extra_overview_diffs": extra,
        "v1_summary": _summarize_payload(v1_payload),
        "v3_summary": _summarize_payload(v3_payload),
    }
