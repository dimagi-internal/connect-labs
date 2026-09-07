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
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError("list_approved_visits requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="connect_csv"),
        grouping_key="entity_id",
        terminal_stage="visit_level",
        filters={"status": ["approved"]},
        fields=[
            FieldComputation(name="form_name", path=_FORM_NAME_PATH, aggregation="first"),
            FieldComputation(name="wa_case_id", paths=WA_CASE_ID_PATHS, aggregation="first"),
            FieldComputation(name="deworming", path=_DEWORMING_PATH, aggregation="first"),
            FieldComputation(name="muac", path=_MUAC_PATH, aggregation="first"),
            FieldComputation(name="vaccination", path=_VACCINATION_PATH, aggregation="first"),
        ],
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    visits = []
    for row in result.rows:
        c = row.computed
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
            }
        )
    return visits


def aggregate_visits_by_wa(visits: list[dict], wa_ids: set[str] | None = None) -> dict[str, dict]:
    """Roll up `list_approved_visits`' rows into one aggregate per
    `wa_case_id`: ``{"approved_hsd_count", "approved_ncf_count",
    "approved_inaccessible_count", "deworming_given", "muac_given",
    "vaccination_given"}`` — the exact fields `core.indicators.wa_rate`
    expects, minus the work-area case properties (ward/status/building_count/
    etc.), which come from `core/work_areas.py` instead.

    `wa_ids`, if given, restricts aggregation to those work areas (Phase 1's
    ward selection) — visits at any other WA are ignored. `None` means no
    restriction (every WA in the pulled visit data)."""
    agg: dict[str, dict] = {}
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
    return agg


def build_evaluation_rows(work_areas: list[dict], visit_aggregates: dict[str, dict]) -> list[dict]:
    """Merge `core.work_areas.list_work_areas`' per-WA case rows with
    `aggregate_visits_by_wa`'s per-WA visit aggregates into
    `core.indicators.evaluate_run`'s expected input shape.

    `lat`/`lon`/`boundary` are left `None`/unset here — `core/candidates.py`'s
    `build_evaluation_input` merges those in afterwards from
    `core.geometry.fetch_work_area_geometry` (this function only knows about
    case data + visit aggregates, not geometry)."""
    zero_agg = {
        "approved_hsd_count": 0,
        "approved_ncf_count": 0,
        "approved_inaccessible_count": 0,
        "deworming_given": 0,
        "muac_given": 0,
        "vaccination_given": 0,
    }
    rows = []
    for wa in work_areas:
        agg = visit_aggregates.get(wa["case_id"], zero_agg)
        rows.append(
            {
                "wa_id": wa["case_id"],
                "ward": wa["ward"],
                "lga": wa["lga"],
                "state": wa["state"],
                "flw_username": wa.get("owner_id", ""),
                "lat": None,
                "lon": None,
                "status": wa["status"],
                "building_count": wa["building_count"],
                "expected_visit_count": wa["expected_visit_count"],
                **agg,
            }
        )
    return rows
