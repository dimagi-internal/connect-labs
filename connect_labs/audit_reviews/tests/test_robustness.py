"""A source report's registry changes without this code changing: odd measures must not break the page.

On 24 Nov 2026 KC Pulse added a range ("mid2") measure and the page answered 500 for everyone. These
tests feed the rules every measure shape KC Pulse publishes today, then deliberately broken ones,
and require a page (with warnings) rather than an error.
"""

import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from connect_labs.audit_reviews import engine, service
from connect_labs.audit_reviews.models import SourceRun

# The 28-day measures as KC Pulse published them on 24 Nov 2026 (run for the week ending 22 Nov):
# ids, units, directions and thresholds only.
KC_MEASURES_2026_11 = [
    ("total_cases_28d", "n", "none", None, 10),
    ("registered_cases_28d", "n", "none", None, 1),
    ("started_cases_28d", "n", "none", None, 1),
    ("visits_per_case_60_28d", "n", "higher", [4.95, 3.05], 10),
    ("pct_disc_60_28d", "%", "lower", [10, 20], 10),
    ("vpc60_visits_n_28d", "n", "none", None, 1),
    ("disc60_n_28d", "n", "none", None, 1),
    ("pct_first_visit_on_time_28d", "%", "higher", [50, 30], 10),
    ("pct_first_visit_on_time_hospital_28d", "%", "higher", [50, 30], 10),
    ("pct_first_visit_on_time_home_other_28d", "%", "higher", [50, 30], 10),
    ("pct_not_visited_28d", "%", "lower", [10, 25], 10),
    ("fv_on_time_n_28d", "n", "none", None, 1),
    ("fv_on_time_hospital_n_28d", "n", "none", None, 1),
    ("fv_on_time_home_other_n_28d", "n", "none", None, 1),
    ("not_visited_n_28d", "n", "none", None, 1),
    ("cs_enrolled_n_28d", "n", "none", None, 1),
    ("cs_awaiting_first_visit_28d", "n", "none", None, 1),
    ("cs_in_progress_28d", "n", "none", None, 1),
    ("cs_min_visits_met_28d", "n", "none", None, 1),
    ("cs_completed_28d", "n", "none", None, 1),
    ("cs_discontinued_28d", "n", "none", None, 1),
    ("cs_lost_to_followup_28d", "n", "none", None, 1),
    ("cs_never_visited_28d", "n", "none", None, 1),
    ("cs_deceased_28d", "n", "none", None, 1),
    ("pct_deceased_d60_28d", "%", "mid2", [[4, 12], [2, 16]], 50),
    ("deceased_d60_n_28d", "n", "none", None, 1),
]
BANDS_SEEN = ["green", "yellow", "red", "insufficient", "nodata", "notcredible", "unbanded"]
SETUP = {
    "pool": {"visits_per_case_60_28d": "vpc60_visits_n_28d", "pct_first_visit_on_time_28d": "fv_on_time_n_28d"},
    "parents": {"pct_disc_60_28d": "visits_per_case_60_28d"},
    "supporting": ["pct_disc_60_28d"],
    "known": ["visits_per_case_60_28d", "pct_disc_60_28d", "pct_first_visit_on_time_28d"],
}


def _measures(rows):
    return [
        {"id": i, "title": i, "unit": u, "direction": d, "bands": b, "min_denominator": md} for i, u, d, b, md in rows
    ]


def _cell(r, mid, unit):
    band = r.choice(BANDS_SEEN)
    value = r.random() if unit == "%" else r.randint(0, 60)
    return {"band": band, "value": value, "n": r.randint(0, 80)}


def _runs(measures, n_runs=6, seed=3, odd_cell=None):
    """Generated runs over these measures: a program, two LLOs, three opportunities, workers on one or two."""
    r = random.Random(seed)
    out = []
    for w in range(n_runs):

        def cells():
            return {m["id"]: (odd_cell(r, m) if odd_cell else _cell(r, m["id"], m["unit"])) for m in measures}

        out.append(
            {
                "run_id": 70000 + w,
                "period_start": str(date(2026, 9, 28) + timedelta(days=7 * w)),
                "period_end": str(date(2026, 10, 4) + timedelta(days=7 * w)),
                "programme": cells(),
                "llo": {"ALPHA": cells(), "BRAVO": cells()},
                "opportunity": {"10901": cells(), "10902": cells(), "10903": cells()},
                "opps": [{"id": 10901, "llo": "ALPHA"}, {"id": 10902, "llo": "ALPHA"}, {"id": 10903, "llo": "BRAVO"}],
                "worker": [
                    {
                        "key": f"{o}::w{i}",
                        "username": f"w{i}",
                        "name": f"Worker {i}",
                        "opp": o,
                        "llo": llo,
                        "cells": cells(),
                    }
                    for i in range(8)
                    for o, llo in ([("10901", "ALPHA")] + ([("10902", "ALPHA")] if i % 3 == 0 else []))
                ],
                "measures": measures,
            }
        )
    return out


def _world(measures, **kw):
    """engine-level: sanitize, configure and replay, as recompute does."""
    runs = _runs(measures, **kw)
    D = {
        "source": {"program_name": "Generated", "opportunities": []},
        "metrics": service._metrics_from(measures),
        "pool": SETUP["pool"],
        "parents": SETUP["parents"],
        "supporting": SETUP["supporting"],
        "knownMetrics": SETUP["known"],
        "runs": runs,
    }
    D, warnings = engine.sanitize(D)
    cfg = engine.default_config(D)
    return engine.replay(D, cfg, {}, {"goLiveRunId": runs[2]["run_id"]}), warnings


# ---------------------------------------------------------------------------
# Today's registry
# ---------------------------------------------------------------------------


def test_todays_kc_pulse_measures_replay_without_warnings():
    world, warnings = _world(_measures(KC_MEASURES_2026_11))
    assert warnings == []
    assert world["pf"]["pct_deceased_d60_28d"] == 100
    assert world["proposalsByRun"][-1] is not None


# ---------------------------------------------------------------------------
# Broken measure definitions
# ---------------------------------------------------------------------------

ODD_DEFINITIONS = [
    ("thresholds as text", {"bands": ["50", "30"]}),
    ("thresholds as an object", {"bands": {"green": 50, "yellow": 30}}),
    ("one threshold", {"bands": [50]}),
    ("ranges of three", {"direction": "mid2", "bands": [[1, 2, 3], [0, 9, 9]]}),
    ("a new direction", {"direction": "mid1", "bands": [4, 12]}),
    ("minimum cases as text", {"min_denominator": "ten"}),
    ("unit as a number", {"unit": 7}),
]


@pytest.mark.parametrize("label,change", ODD_DEFINITIONS, ids=[c[0] for c in ODD_DEFINITIONS])
def test_an_odd_measure_definition_is_set_aside_not_fatal(label, change):
    rows = _measures(KC_MEASURES_2026_11)
    for m in rows:  # break a detail AND a main metric
        if m["id"] in ("pct_not_visited_28d", "pct_first_visit_on_time_28d"):
            m.update(change)
    world, warnings = _world(rows)
    assert world["history"]["program"], label
    if change.get("bands") is not None or change.get("direction") == "mid1":
        assert any("pct_not_visited_28d" in w for w in warnings), warnings


def test_a_measure_whose_definition_is_not_an_object_is_left_out():
    D = {"metrics": {"m": "oops", "ok": {"unit": "n", "bands": None}}, "runs": []}
    D, warnings = engine.sanitize(D)
    assert list(D["metrics"]) == ["ok"] and "m:" in warnings[0]


# ---------------------------------------------------------------------------
# Broken figures
# ---------------------------------------------------------------------------

ODD_CELLS = [
    ("value as text", lambda r, m: {"band": "red", "value": "0.4", "n": 20}),
    ("count as text", lambda r, m: {"band": "red", "value": 0.4, "n": "20"}),
    ("band as a number", lambda r, m: {"band": 3, "value": 0.4, "n": 20}),
    ("band missing", lambda r, m: {"value": 0.4, "n": 20}),
    ("cell as a list", lambda r, m: [0.4, 20]),
    ("cell as null", lambda r, m: None),
    ("an unknown band", lambda r, m: {"band": "amber", "value": 0.4, "n": 20}),
    ("NaN-like values", lambda r, m: {"band": "red", "value": float("inf"), "n": 20}),
]


@pytest.mark.parametrize("label,make", ODD_CELLS, ids=[c[0] for c in ODD_CELLS])
def test_odd_figures_never_break_the_replay(label, make):
    def odd(r, m):  # mostly normal figures, some odd ones
        return make(r, m) if r.random() < 0.3 else _cell(r, m["id"], m["unit"])

    world, warnings = _world(_measures(KC_MEASURES_2026_11), odd_cell=odd)
    assert world["history"]["program"], label


def test_odd_figures_are_counted_in_a_warning():
    def odd(r, m):
        return (
            {"band": "red", "value": "x", "n": 20}
            if m["id"] == "pct_not_visited_28d"
            else _cell(r, m["id"], m["unit"])
        )

    _, warnings = _world(_measures(KC_MEASURES_2026_11), odd_cell=odd)
    assert any(w.startswith("pct_not_visited_28d:") and "left blank" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# Through the service: the page still opens, and says what it left out
# ---------------------------------------------------------------------------


@pytest.fixture
def source(db):
    d = SimpleNamespace(
        id=950,
        data={"config": {"audit_source_workflow_id": 27270, "read_workflows": [27270], "audit_setup": SETUP}},
    )
    src = service.open_source(d, opportunity_id=10901)
    src.go_live_run_id = 70002
    src.save()
    return src


def _store(source, runs):
    for r in runs:
        data = {k: v for k, v in r.items() if k not in ("run_id", "period_start", "period_end")}
        SourceRun.objects.create(
            source=source,
            run_id=r["run_id"],
            period_start=r["period_start"],
            period_end=r["period_end"],
            completed_at=f"{r['period_end']}T09:00:00Z",
            data=data,
        )


def test_the_page_shows_what_it_set_aside(source):
    rows = _measures(KC_MEASURES_2026_11)
    next(m for m in rows if m["id"] == "pct_disc_60_28d")["bands"] = {"oops": 1}
    _store(source, _runs(rows))
    payload = service.world(source)
    assert payload["runs"] and any("pct_disc_60_28d" in w for w in payload["warnings"])


def test_if_the_rules_still_fail_the_main_metrics_are_shown(source, monkeypatch):
    _store(source, _runs(_measures(KC_MEASURES_2026_11)))
    real = engine.replay

    def fails_with_details(D, CFG, *a, **k):
        if any(v.get("role") != "main" for v in CFG["metrics"].values()):
            raise ValueError("something nobody foresaw")
        return real(D, CFG, *a, **k)

    monkeypatch.setattr(engine, "replay", fails_with_details)
    payload = service.world(source)
    assert set(payload["CFG"]["metrics"]) == {"visits_per_case_60_28d", "pct_first_visit_on_time_28d"}
    assert any("only the main metrics" in w and "ValueError" in w for w in payload["warnings"])


def test_a_run_with_an_unreadable_end_date_is_skipped(source):
    runs = _runs(_measures(KC_MEASURES_2026_11))
    runs[3]["period_end"] = "22/11/2026"
    _store(source, runs)
    payload = service.world(source)
    assert len(payload["runs"]) == len(runs) - 1
