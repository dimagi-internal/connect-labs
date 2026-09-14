"""Approved HSD/NCF/Inaccessible visit-form data, aggregated per work area —
the expensive pull (design brief §5), scoped in application logic to
whichever ward(s) Phase 1 selected. Real field paths re-verified this
session against `core/areas.py`'s `ward_children_per_building` (which already
pulls HSD visits the same way) — re-verify again before relying on them, per
the brief's own caveat: these evolve.
"""

from __future__ import annotations

from django.http import HttpRequest

from connect_labs.mopup.core.areas import HSD_FORM_NAME, WA_CASE_ID_PATHS

NCF_FORM_NAME = "No Children Found"
INACCESSIBLE_FORM_NAME = "Inaccessible WA"

_FORM_NAME_PATH = "form.@name"
_DEWORMING_PATH = "form.case.update.dw_meds_delivery_status"
_DEWORMING_GIVEN_VALUE = "DW Delivered"
_MUAC_PATH = "form.case.update.soliciter_muac_cm"
_VACCINATION_PATH = "form.case.update.received_any_vaccine"
_VACCINATION_GIVEN_VALUE = "yes"


def list_approved_visits(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> list[dict]:
    """One dict per approved HSD/NCF/Inaccessible visit in `opportunity_id`:
    ``{"wa_case_id", "form_name", "deworming_given", "muac_recorded",
    "vaccination_given"}``.

    This is the expensive pull — the whole opportunity's approved visit-form
    data, cached by `AnalysisPipeline` the same way `core/work_areas.py`'s
    cheap case pull is (see that module's docstring). There is no ward or
    date narrowing at fetch time (verified this session — see the mop-up
    design brief §4/§11); scope to a ward/date range by filtering the
    returned list in Python (`aggregate_visits_by_wa` takes a pre-filtered
    `wa_case_id` allowlist for exactly this).
    """
    from connect_labs.labs.analysis.config import (
        AnalysisPipelineConfig,
        CacheStage,
        DataSourceConfig,
        FieldComputation,
    )
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError("list_approved_visits requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="connect_csv"),
        grouping_key="entity_id",
        # See core/geometry.py's identical comment: must be the enum, not the
        # string "visit_level" — backend.py's process_and_cache dispatch is
        # an enum comparison, and a bare string here silently falls through
        # to FLW aggregation on a cache miss.
        terminal_stage=CacheStage.VISIT_LEVEL,
        filters={"status": ["approved"]},
        fields=[
            FieldComputation(name="form_name", path=_FORM_NAME_PATH, aggregation="first"),
            FieldComputation(name="wa_case_id", paths=WA_CASE_ID_PATHS, aggregation="first"),
            FieldComputation(name="deworming", path=_DEWORMING_PATH, aggregation="first"),
            FieldComputation(name="muac", path=_MUAC_PATH, aggregation="first"),
            FieldComputation(name="vaccination", path=_VACCINATION_PATH, aggregation="first"),
        ],
        # Real production bug, found live this session: without this, this
        # ad-hoc config shares ONE raw-visit-cache slot per opportunity with
        # every other ad-hoc caller in this app (list_work_areas,
        # fetch_work_area_geometry included) — whichever runs last within
        # one build_evaluation_input call wholesale-clobbers what the others
        # just wrote, exactly the `AnalysisPipelineConfig.pipeline_id`
        # docstring's own documented "issue #116" pattern (confirmed live:
        # every work area's case id came back null, and concurrent fetches
        # raised a "Concurrent write to ComputedVisitCache" IntegrityError).
        # 12968 is the existing "CHC Approved Visits" pipeline definition
        # for this exact data_source/filter/field shape.
        pipeline_id=12968,
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    visits = []
    for row in result.rows:
        # `row.computed` is `None` (not `{}`) for some real visit forms where
        # field extraction found nothing to compute — confirmed against real
        # program-217 data this session.
        c = row.computed or {}
        form_name = c.get("form_name")
        if form_name not in (HSD_FORM_NAME, NCF_FORM_NAME, INACCESSIBLE_FORM_NAME):
            continue
        visits.append(
            {
                "wa_case_id": c.get("wa_case_id"),
                "form_name": form_name,
                "deworming_given": c.get("deworming") == _DEWORMING_GIVEN_VALUE,
                "muac_recorded": bool(c.get("muac")),
                "vaccination_given": c.get("vaccination") == _VACCINATION_GIVEN_VALUE,
                # Both already free on every row (same tier as entity_id/
                # entity_name -- see AnalysisRow.username/.visit_date), not
                # FieldComputation outputs -- this is the actual submitting
                # FLW's Connect username, unlike the work-area CASE's own
                # `owner_id` (a raw CommCare HQ user UUID in a different
                # identifier space that `fetch_flw_names()` can never
                # resolve). See `aggregate_visits_by_wa`'s use of these two.
                "username": row.username,
                "visit_date": row.visit_date,
            }
        )
    return visits


def aggregate_visits_by_wa(visits: list[dict], wa_ids: set[str] | None = None) -> dict[str, dict]:
    """Roll up `list_approved_visits`' rows into one aggregate per
    `wa_case_id`: ``{"approved_hsd_count", "approved_ncf_count",
    "approved_inaccessible_count", "deworming_given", "muac_given",
    "vaccination_given", "flw_username"}`` — the count fields are the exact
    ones `core.indicators.wa_rate` expects, minus the work-area case
    properties (ward/status/building_count/etc.), which come from
    `core/work_areas.py` instead.

    `flw_username` is the Connect username of the work area's LAST
    submitter — its own visits' `username`, ordered by `visit_date`, not the
    work-area case's `owner_id` (see `list_approved_visits`'s comment on why
    that distinction matters for name resolution). It shouldn't normally
    happen that two different FLWs submit to the same work area, but if it
    does, the most RECENT submitter wins over one just seen more often, on
    the theory that a WA's current worker is more useful to show than
    whoever historically logged the most visits there. A visit with no
    `visit_date` never displaces an already-picked submitter (nothing to
    compare it against); the first username-bearing visit encountered is
    the initial pick regardless, so a WA where every visit lacks a date
    still gets *a* submitter, not none.

    `wa_ids`, if given, restricts aggregation to those work areas (Phase 1's
    ward selection) — visits at any other WA are ignored. `None` means no
    restriction (every WA in the pulled visit data)."""
    agg: dict[str, dict] = {}
    # (visit_date, username) of the current "last submitter" pick per WA --
    # kept separate from `agg` so that internal bookkeeping never leaks into
    # the returned per-WA dict shape.
    latest_by_wa: dict[str, tuple] = {}
    for v in visits:
        wa_id = v.get("wa_case_id")
        if not wa_id:
            continue
        if wa_ids is not None and wa_id not in wa_ids:
            continue
        row = agg.setdefault(
            wa_id,
            {
                "approved_hsd_count": 0,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
                "deworming_given": 0,
                "muac_given": 0,
                "vaccination_given": 0,
                "flw_username": "",
            },
        )
        if v["form_name"] == HSD_FORM_NAME:
            row["approved_hsd_count"] += 1
            if v.get("deworming_given"):
                row["deworming_given"] += 1
            if v.get("muac_recorded"):
                row["muac_given"] += 1
            if v.get("vaccination_given"):
                row["vaccination_given"] += 1
        elif v["form_name"] == NCF_FORM_NAME:
            row["approved_ncf_count"] += 1
        elif v["form_name"] == INACCESSIBLE_FORM_NAME:
            row["approved_inaccessible_count"] += 1

        username = v.get("username")
        if username:
            visit_date = v.get("visit_date")
            prev = latest_by_wa.get(wa_id)
            if prev is None or (visit_date is not None and (prev[0] is None or visit_date > prev[0])):
                latest_by_wa[wa_id] = (visit_date, username)
                row["flw_username"] = username
    return agg


def build_evaluation_rows(work_areas: list[dict], visit_aggregates: dict[str, dict]) -> list[dict]:
    """Merge `core.work_areas.list_work_areas`' per-WA case rows with
    `aggregate_visits_by_wa`'s per-WA visit aggregates into
    `core.indicators.evaluate_run`'s expected input shape.

    `lat`/`lon`/`boundary` are left `None`/unset here — `core/candidates.py`'s
    `build_evaluation_input` merges those in afterwards from
    `core.geometry.fetch_work_area_geometry` (this function only knows about
    case data + visit aggregates, not geometry).

    `flw_username` prefers `aggregate_visits_by_wa`'s visit-derived value
    (the last submitter's Connect username — the id space
    `labs.analysis.data_access.fetch_flw_names()` can actually resolve to a
    display name) and falls back to the work-area case's own `owner_id`
    (a raw CommCare HQ user UUID, never resolvable to a name) only for a WA
    with no approved visits to derive a submitter from."""
    zero_agg = {
        "approved_hsd_count": 0,
        "approved_ncf_count": 0,
        "approved_inaccessible_count": 0,
        "deworming_given": 0,
        "muac_given": 0,
        "vaccination_given": 0,
        "flw_username": "",
    }
    rows = []
    for wa in work_areas:
        agg = visit_aggregates.get(wa["case_id"], zero_agg)
        rows.append(
            {
                "wa_id": wa["case_id"],
                "wa_name": wa.get("wa_name", ""),
                "ward": wa["ward"],
                "lga": wa["lga"],
                "state": wa["state"],
                "lat": None,
                "lon": None,
                "status": wa["status"],
                "building_count": wa["building_count"],
                "expected_visit_count": wa["expected_visit_count"],
                **agg,
                "flw_username": agg.get("flw_username") or wa.get("owner_id", ""),
            }
        )
    return rows
