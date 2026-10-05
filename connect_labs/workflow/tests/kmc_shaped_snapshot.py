"""A KMC-programme-shaped saved-run snapshot, built by the REAL semantic builder.

Shared by the snapshot-storage tests. Sized like the KMC programme report the
child-record storage exists for: 24 headline (C) indicators, a 12-indicator
scorecard (N) series, 5 LLOs over 10 opportunities, 150 workers, 12 cohort months
and a ~9k-case index -- so sizes measured against it mean something.
"""

from __future__ import annotations

import random

from connect_labs.semantic import snapshot as snap
from connect_labs.workflow.snapshot_builders import wrap_for_runner

_LLOS = ["EHA", "GHI", "KMCN", "PATH", "SWA"]
_OPPS = list(range(10011, 10021))
_LLO_MAP = {opp: _LLOS[i % len(_LLOS)] for i, opp in enumerate(_OPPS)}
_DEPLOY = {"llo_map": _LLO_MAP, "settings": {}, "app_asks": {}, "asks_as": {}}
_MONTHS = [f"2025-{m:02d}-01" for m in range(10, 13)] + [f"2026-{m:02d}-01" for m in range(1, 10)]


def _measures(prefix: str, n: int) -> list[dict]:
    return [
        {
            "id": f"{prefix.lower()}{i:02d}",
            "indicator": f"{prefix}{i:02d}",
            "series": prefix,
            "label": f"{prefix}{i:02d} indicator with a realistic human-readable label",
            "unit": "%",
            "direction": "higher",
            "bands": [70, 50],
            "min_denominator": 25,
            "inputs": [],
        }
        for i in range(1, n + 1)
    ]


def _row(rng: random.Random, measures: list[dict], **scope) -> dict:
    row = dict(scope, n_cases=rng.randint(30, 900))
    for m in measures:
        row[m["id"]] = round(rng.uniform(20, 99), 1)
        row[f"{m['id']}_denominator"] = rng.randint(10, 800)
    return row


def kmc_shaped_snapshot(n_cases: int = 9000, embed_cases: bool = True) -> dict:
    rng = random.Random(42)
    c_measures, n_measures = _measures("C", 24), _measures("N", 12)
    every = c_measures + n_measures
    rows = [_row(rng, every, scope="programme")]
    rows += [_row(rng, every, scope="llo", llo=llo) for llo in _LLOS]
    rows += [_row(rng, every, scope="opportunity", opportunity_id=opp) for opp in _OPPS]
    flws = [(opp, f"flw_{opp}_{i:02d}") for opp in _OPPS for i in range(15)]
    rows += [_row(rng, every, scope="flw", opportunity_id=opp, username=u) for opp, u in flws]
    for month in _MONTHS:
        rows.append(_row(rng, every, scope="month", cohort_month=month))
        rows += [_row(rng, every, scope="llo_month", llo=llo, cohort_month=month) for llo in _LLOS]
        rows += [_row(rng, every, scope="opportunity_month", opportunity_id=opp, cohort_month=month) for opp in _OPPS]
    spec = {
        "builder": "semantic_snapshot",
        "series": "C",
        "scopes": ["programme", "llo", "opportunity", "flw"],
        "case_index": {
            "pipeline": "children",
            "fields": ["entity_id", "username", "opportunity_id", "reg_date", "birth_weight_g", "total_visits"],
        },
        "visits_pipeline": "visits",
        "min_denominator_default": 25,
    }
    children = []
    for i in range(n_cases):
        opp, user = flws[i % len(flws)]
        children.append(
            {
                "entity_id": f"case-{i:06d}",
                "username": user,
                "opportunity_id": opp,
                "reg_date": f"2026-0{1 + i % 9}-{1 + i % 28:02d}",
                "birth_weight_g": rng.randint(900, 2500),
                "total_visits": rng.randint(0, 8),
            }
        )
    visits = [
        {"opportunity_id": flws[i % len(flws)][0], "visit_date": f"2026-0{1 + i % 9}-{1 + i % 28:02d}"}
        for i in range(n_cases * 3)
    ]
    cases = snap.case_rows({"children": {"rows": children}}, spec, _LLO_MAP)
    payload = snap.build(
        spec=spec,
        rows=rows,
        measures=c_measures,
        deployment=_DEPLOY,
        cases=cases,
        meta={"as_of": "2026-09-28", "cases": len(cases), "registry": {"registry_id": 77}},
        generated_at="2026-09-28T00:00:00Z",
        visit_rows=visits,
        extra_series={"N": n_measures},
        as_of="2026-09-28",
        embed_cases=embed_cases,
    )
    return wrap_for_runner(payload, "snapshot")
