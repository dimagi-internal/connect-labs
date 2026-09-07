"""Phase 2's live "how many work areas would this create" recompute: fetch
(cache-warmed after the first call — see `core/work_areas.py`/`core/visits.py`
docstrings) the run's scoped work areas + visits, evaluate against whatever
indicator/global config the reviewer currently has set, and return candidates
+ a per-ward summary rollup.

**Known gap, not an oversight**: only WARD scoping is wired up here. A run's
`date_from`/`date_to` (Phase 1) isn't applied yet — `AnalysisPipelineConfig`'s
`date_from`/`date_to` window is only wired into the FLW-rollup aggregation
path (`get_period_scoped_flw_result`), not the raw entity/visit_level pull
this app uses (verified this session). Applying a date bound here needs a
`visit_date` field extracted per visit and filtered in Python, the same way
ward-scoping already is — not built yet since program 217's own round uses
all-time data regardless (§4/§11 of the design brief).
"""

from __future__ import annotations

from django.http import HttpRequest

from connect_labs.mopup.core.visits import aggregate_visits_by_wa, build_evaluation_rows, list_approved_visits
from connect_labs.mopup.core.work_areas import list_work_areas


def _ward_matches(wa: dict, selected_wards: list[dict]) -> bool:
    if not selected_wards:
        return True  # no selection -> every ward
    return any(
        wa["ward"] == sw.get("ward") and wa["lga"] == sw.get("lga") and wa["state"] == sw.get("state")
        for sw in selected_wards
    )


def build_evaluation_input(
    opportunity_id: int,
    selected_wards: list[dict],
    *,
    request: HttpRequest | None = None,
) -> list[dict]:
    """The full per-WA row list, scoped to `selected_wards` (empty = every
    ward in the opportunity), ready for `core.indicators.evaluate_run`."""
    work_areas = list_work_areas(opportunity_id, request=request)
    scoped = [wa for wa in work_areas if _ward_matches(wa, selected_wards)]
    wa_ids = {wa["case_id"] for wa in scoped}

    visits = list_approved_visits(opportunity_id, request=request)
    aggregates = aggregate_visits_by_wa(visits, wa_ids=wa_ids)

    return build_evaluation_rows(scoped, aggregates)


def summarize_candidates_by_ward(candidates: list[dict], all_rows: list[dict]) -> list[dict]:
    """Per-ward rollup for the candidate table (design brief §8): total work
    areas reviewed, how many are candidates, and how many were flagged by 2+
    indicators (§6c) — cheap since severity is already computed per
    candidate."""
    totals: dict[tuple[str, str, str], int] = {}
    for wa in all_rows:
        key = (wa["state"], wa["lga"], wa["ward"])
        totals[key] = totals.get(key, 0) + 1

    by_ward: dict[tuple[str, str, str], dict] = {}
    for c in candidates:
        key = (c["state"], c["lga"], c["ward"])
        row = by_ward.setdefault(
            key,
            {
                "ward": c["ward"],
                "lga": c["lga"],
                "state": c["state"],
                "total_work_areas": totals.get(key, 0),
                "candidate_count": 0,
                "flagged_by_2_plus": 0,
            },
        )
        row["candidate_count"] += 1
        if c["severity_count"] >= 2:
            row["flagged_by_2_plus"] += 1

    return sorted(by_ward.values(), key=lambda r: (r["state"], r["lga"], r["ward"]))
