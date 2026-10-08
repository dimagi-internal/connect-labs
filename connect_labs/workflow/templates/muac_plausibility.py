"""MUAC/Age Plausibility -- Program 217 (CHC - NG - RCT - Aug 2026).

Flags MUAC readings that are biologically implausible for the child's recorded age,
as a data-quality indicator alongside the program's audit reports, and breaks the
rate down by LLO, ward and FLW with the numerator and denominator beside every
percentage.

How it is built, and why:

* **Classified on the server, not in the browser.** The program has about a quarter
  of a million visits, and a visit-level row carries the child's name and ~500
  bytes of base columns. ``run_default`` reads the one pipeline below, classifies
  every reading with ``workflow/muac_plausibility_compute.py`` (where every
  threshold lives, with its rationale), and saves a program-owned run holding one
  row per (opportunity, ward, FLW, week, age band) cell -- counts only, no child
  data. The render filters and re-aggregates those cells, so the date range, LLO,
  ward and age-band filters are instant.
* **One run per refresh.** Schedule it daily (the Schedule button on the workflow
  list) or run it with the MCP ``workflow_run_default`` tool. The page reads the
  newest completed run, and the week column carries the trend.
* **LLO is the opportunity's organisation.** Program 217 has one opportunity per
  LLO plus an R1 opportunity for each; ``config.llo_by_opportunity`` names the LLO
  for each opportunity id so the two roll up together.
* **Ward is the visit's own** (``form.work_area_info.wa_ward``), captured at
  submission, so an FLW who worked in two wards appears under both, and the FLW
  table adds their wards up.
* **"CHC approved" is Connect's visit status ``approved``.** Rejected, pending and
  over-limit visits are not in the denominator.

Colour coding is the one-sided 95% test the audit indicators use: with ``p`` the
program-wide Tier A rate over the selected weeks and ages, a unit is red when its
rate exceeds ``p + 1.645 * sqrt(p(1-p)/n)``, and grey when ``n`` is below
``config.min_n``. A fixed-percentage scheme is available as a toggle; the page
always says which one is driving the colours.

Design brief: CHC_Program217_MUAC_Plausibility_Dashboard_Guide (sections 2-6).
"""

from __future__ import annotations

from pathlib import Path

RENDER_CODE = (Path(__file__).parent / "muac_plausibility_render.js").read_text(encoding="utf-8")

STATE_KEY = "muac_plausibility"

PIPELINE_SCHEMAS = [
    {
        "alias": "muac_visits",
        "name": "CHC approved visits -- MUAC and age",
        "description": (
            "One row per Connect-approved visit with the MUAC reading, the child's age, sex and the "
            "visit's ward. Read only by the MUAC plausibility run, which keeps counts, not rows."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {"status": ["approved"]},
            "fields": [
                {"name": "form_name", "path": "form.@name", "aggregation": "first"},
                {
                    "name": "muac_cm",
                    "path": "form.case.update.soliciter_muac_cm",
                    "aggregation": "first",
                    "description": "MUAC as typed, in cm. Kept as text so a non-number counts as missing.",
                },
                {
                    "name": "age_months",
                    "paths": [
                        "form.additional_case_info.childs_age_in_months",
                        "form.case.update.childs_age_in_months",
                    ],
                    "aggregation": "first",
                    "description": "Age in months, computed by the form at the visit.",
                },
                {
                    "name": "childs_dob",
                    "path": "form.additional_case_info.childs_dob",
                    "aggregation": "first",
                    "description": "Fallback when the age is missing: age at the visit date from this.",
                },
                {
                    "name": "sex",
                    "paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
                    "aggregation": "first",
                },
                {
                    "name": "ward",
                    "path": "form.work_area_info.wa_ward",
                    "aggregation": "first",
                    "description": "Ward of the work area, as captured on the visit.",
                },
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
            ],
        },
    },
]

DEFINITION = {
    "name": "MUAC/Age Plausibility",
    "description": (
        "Share of CHC-approved MUAC readings that are implausible for the child's age, by LLO, "
        "ward and FLW, with statistically elevated units highlighted. Readings below WHO -2SD "
        "(possible real malnutrition), mm/cm unit mix-ups and missing data are shown separately."
    ),
    "version": 1,
    "templateType": "muac_plausibility",
    "statuses": [],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        "templateType": "muac_plausibility",
        # Lagos, UTC+1, no daylight saving: weeks run Monday-Sunday WAT.
        "utc_offset_hours": 1,
        # Below this many valid readings a unit is shown grey: the test means nothing.
        "min_n": 20,
        # "observed": p is the program-wide Tier A rate over the selected weeks and
        # ages. "fixed": p is baseline_fixed (the ~0.3% reference-population rate).
        "baseline_mode": "observed",
        "baseline_fixed": 0.003,
        # The fallback fixed-% colour scheme: green <= first, yellow <= second, red above.
        "fixed_pct_bands": [0.01, 0.05],
        # {"<opportunity id>": "<LLO name>"}; an opportunity missing here shows as its id.
        "llo_by_opportunity": {},
    },
    "pipeline_sources": [],
    "snapshot_inputs": {"pipelines": [], "workers": False, "state_keys": [STATE_KEY]},
}

SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        f"state.{STATE_KEY}.generated_at": "ISO timestamp this run was computed",
        f"state.{STATE_KEY}.cells": "{columns, rows}: counts per (opportunity, ward, FLW, week, age band)",
        f"state.{STATE_KEY}.flw_names": "{opportunity id: {username: display name}}",
        f"state.{STATE_KEY}.thresholds": "The thresholds the counts were classified with",
        f"state.{STATE_KEY}.sex_missing": "Valid readings with no recorded sex (most conservative ceiling used)",
        f"state.{STATE_KEY}.errors": "Opportunities whose data could not be read for this run",
    },
}


def run_default(*, definition, access_token, request=None, window=None, **_):
    """Classify every approved MUAC reading to date and save one completed,
    program-owned run.

    ``window`` (UTC half-open pair) narrows the visits read, for a backfill; by
    default every visit up to now is read, since the page filters by week itself.
    An opportunity whose pipeline read fails is named in the run's ``errors`` and
    in the returned ``errors`` (the scheduler shows these as an amber note), never
    silently counted as zero.
    """
    from datetime import datetime, timedelta, timezone

    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import FORM_NAME
    from connect_labs.workflow.muac_plausibility_compute import aggregate, thresholds_summary

    opp_ids = definition.opportunity_ids or ([definition.opportunity_id] if definition.opportunity_id else [])
    if not opp_ids:
        raise ValueError("muac_plausibility requires at least one opportunity on the definition")
    config = (getattr(definition, "data", None) or {}).get("config") or {}
    utc_offset = timedelta(hours=float(config.get("utc_offset_hours", 1)))

    if definition.program_id:
        wda = WorkflowDataAccess(access_token=access_token, program_id=definition.program_id)
    else:
        wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_ids[0])
    errors: list[str] = []
    flw_names: dict[str, dict[str, str]] = {}
    try:
        pipeline_data = wda.get_pipeline_data(definition.id, opportunity_id=opp_ids[0])
        source = pipeline_data.get("muac_visits", {})
        per_opp = source.get("metadata", {}).get("per_opp", {})
        for opp_id in opp_ids:
            meta = per_opp.get(str(opp_id)) or {}
            if meta.get("error"):
                errors.append(f"visits unavailable for opportunity {opp_id}: {meta['error']}")
            elif meta.get("raw_fetch_anomaly"):
                errors.append(f"short read for opportunity {opp_id}: {meta['raw_fetch_anomaly']}")
            try:
                flw_names[str(opp_id)] = {
                    w["username"]: w.get("name") or w["username"] for w in wda.get_workers(opp_id) if w.get("username")
                }
            except Exception as exc:  # names are a nicety; usernames still show
                errors.append(f"FLW names unavailable for opportunity {opp_id}: {exc}")
    finally:
        wda.close()

    rows = source.get("rows", [])
    if window is not None:
        window_start, window_end = window

        def _in_window(row):
            try:
                at = datetime.fromisoformat(str(row.get("time_start")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return False
            return window_start <= at < window_end

        rows = [r for r in rows if _in_window(r)]

    cells = aggregate(rows, form_name=FORM_NAME, utc_offset=utc_offset)
    now = datetime.now(timezone.utc)
    weeks = sorted({row[3] for row in cells["rows"]})
    period_start = weeks[0] if weeks else now.date().isoformat()
    period_end = now.date().isoformat()

    state = {
        "generated_at": now.isoformat(),
        "cells": {"columns": cells["columns"], "rows": cells["rows"]},
        "sex_missing": cells["sex_missing"],
        "flw_names": flw_names,
        "thresholds": thresholds_summary(),
        "errors": errors,
    }

    if definition.program_id:
        run_wda = WorkflowDataAccess(access_token=access_token, program_id=definition.program_id)
        scope = {"program_id": definition.program_id}
    else:
        run_wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_ids[0])
        scope = {"opportunity_id": opp_ids[0]}
    try:
        run = run_wda.create_run(
            definition.id, period_start=period_start, period_end=period_end, initial_state={}, **scope
        )
        run_wda.complete_run(run.id, {"pipelines": {}, "workers": [], "state": {STATE_KEY: state}}, run=run)
    finally:
        run_wda.close()

    return {"run_id": run.id, "cells": len(cells["rows"]), "visits_read": len(rows), "errors": errors}


TEMPLATE = {
    "key": "muac_plausibility",
    "name": "MUAC/Age Plausibility",
    "description": DEFINITION["description"],
    "icon": "fa-ruler",
    "color": "orange",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
    "supports_saved_runs": True,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "supports_default_run": True,
    "run_default": run_default,
}
