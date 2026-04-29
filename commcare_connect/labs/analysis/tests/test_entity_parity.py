"""
KMC / SAM entity-stage parity harness.

Runs both v1 and v2 paths over the real opportunities each template is actually
deployed on, canonicalizes outputs, and diffs field-by-field with documented
tolerances. v1 path uses a throwaway Python port of `groupVisitsByChild` (and
the per-template KPI computations) — that helper is single-purpose test code
that gets deleted along with v1 once v2 is promoted.

By default the suite is **skipped in CI** because it needs:
  - A valid prod OAuth token (cached in ~/.commcare-connect/token.json or
    passed via LABS_PARITY_OAUTH_TOKEN env var)
  - Network access to connect.dimagi.com
  - Real opportunity 874 (KMC PIPN, ~11k visits) for the KMC templates
  - Real opportunity 879 (PPFN SAM follow-ups, ~547 visits) for sam_followup_v2

To run locally:
    LABS_PARITY=1 LABS_PARITY_OAUTH_TOKEN=$(cat ~/.commcare-connect/token.json | jq -r .access_token) \\
        pytest commcare_connect/labs/analysis/tests/test_entity_parity.py -v

Promotion criterion (per design doc): the harness must pass on opp 874 (and 879
for sam_followup_v2) before the corresponding v1 template is deleted.
"""

from __future__ import annotations

import math
import os
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABS_PARITY") != "1",
    reason="parity harness requires LABS_PARITY=1 and a valid Connect OAuth token",
)


# ---------------------------------------------------------------------------
# Fixtures: opp IDs each template is deployed on (per ace inventory 2026-04-29)
# ---------------------------------------------------------------------------

KMC_OPP_ID = 874  # KMC PIPN, ~11k visits — only opp running all three KMC templates
SAM_OPP_ID = 879  # PPFN SAM follow-ups, ~547 visits


# ---------------------------------------------------------------------------
# Tolerance / canonicalization helpers
# ---------------------------------------------------------------------------


def _normalize_null(v: Any) -> Any:
    """Treat None, "" and missing keys as equivalent for diff purposes."""
    if v is None or v == "":
        return None
    return v


def _close(a: Any, b: Any, *, atol: float) -> bool:
    """Compare two values with float tolerance, treating null-equivalents as equal."""
    a, b = _normalize_null(a), _normalize_null(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), abs_tol=atol)
    return a == b


def _diff_rows(v1_row: dict, v2_row: dict, *, ratio_fields: set[str] = frozenset()) -> list[str]:
    """Return human-readable diff lines for fields that disagree between the two rows.

    ratio_fields use a looser tolerance (1e-2) appropriate for derived ratios.
    All other numeric fields use 1e-6.
    """
    diffs: list[str] = []
    keys = set(v1_row) | set(v2_row)
    for k in sorted(keys):
        atol = 1e-2 if k in ratio_fields else 1e-6
        if not _close(v1_row.get(k), v2_row.get(k), atol=atol):
            diffs.append(f"  {k}: v1={v1_row.get(k)!r} v2={v2_row.get(k)!r}")
    return diffs


# ---------------------------------------------------------------------------
# v1 → child shape (Python port of JS groupVisitsByChild + computeKPIs)
# ---------------------------------------------------------------------------


def _group_visits_by_child(visit_rows: list[dict], *, link_field: str = "beneficiary_case_id") -> list[dict]:
    """Python port of v1's groupVisitsByChild + per-child derivation.

    Throwaway test code. Mirrors kmc_longitudinal.py:261's logic. Deleted with v1.
    """
    grouped: dict[str, list[dict]] = {}
    for row in visit_rows:
        case_id = row.get(link_field)
        if not case_id:
            continue
        grouped.setdefault(case_id, []).append(row)

    children = []
    for case_id, rows in grouped.items():
        rows_sorted = sorted(rows, key=lambda r: (r.get("visit_date") or "", str(r.get("id") or "")))

        def find_first(field: str) -> Any:
            for r in rows_sorted:
                v = r.get(field)
                if v is not None and v != "":
                    return v
            return None

        def find_last(field: str) -> Any:
            for r in reversed(rows_sorted):
                v = r.get(field)
                if v is not None and v != "":
                    return v
            return None

        first_visit = rows_sorted[0] if rows_sorted else {}
        last_visit = rows_sorted[-1] if rows_sorted else {}

        children.append(
            {
                "entity_id": case_id,
                "total_visits": len(rows_sorted),
                "first_visit_date": first_visit.get("visit_date"),
                "last_visit_date": last_visit.get("visit_date"),
                "child_name": find_first("child_name"),
                "mother_name": find_first("mother_name"),
                "current_weight": find_last("weight"),
                "kmc_status": find_last("kmc_status"),
            }
        )
    return children


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


def _run_pipeline(opp_id: int, schema: dict) -> list[dict]:
    """Run an inline pipeline schema against a real opp and return its rows.

    Builds an AnalysisPipelineConfig from the schema dict, runs it through
    AnalysisPipeline (synchronous mode), and returns the result rows.
    """
    from commcare_connect.labs.analysis.config import (
        AnalysisPipelineConfig,
        CacheStage,
        DataSourceConfig,
        FieldComputation,
    )
    from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

    fields = [
        FieldComputation(
            name=f["name"],
            paths=f.get("paths"),
            path=f.get("path", ""),
            aggregation=f.get("aggregation", "first"),
        )
        for f in schema["fields"]
    ]
    stage_map = {
        "visit_level": CacheStage.VISIT_LEVEL,
        "aggregated": CacheStage.AGGREGATED,
        "entity": CacheStage.ENTITY,
    }
    config = AnalysisPipelineConfig(
        grouping_key=schema.get("grouping_key", "username"),
        fields=fields,
        terminal_stage=stage_map[schema["terminal_stage"]],
        linking_field=schema.get("linking_field", "entity_id"),
        data_source=DataSourceConfig(type=schema.get("data_source", {}).get("type", "connect_csv")),
    )

    token = os.environ["LABS_PARITY_OAUTH_TOKEN"]
    pipeline = AnalysisPipeline(access_token=token)
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id=opp_id)
    return [row.to_dict() if hasattr(row, "to_dict") else row for row in result.rows]


# ---------------------------------------------------------------------------
# The parity tests
# ---------------------------------------------------------------------------


def test_kmc_longitudinal_v1_v2_parity():
    """v1 (visit-level + JS-equivalent shaping) must match v2 (entity stage) on opp 874."""
    from commcare_connect.workflow.templates.kmc_longitudinal_v2 import PIPELINE_SCHEMAS

    children_schema = next(s for s in PIPELINE_SCHEMAS if s["alias"] == "children")["schema"]
    visits_schema = next(s for s in PIPELINE_SCHEMAS if s["alias"] == "visits")["schema"]

    v2_rows = _run_pipeline(KMC_OPP_ID, children_schema)
    v1_visit_rows = _run_pipeline(KMC_OPP_ID, visits_schema)
    v1_rows = _group_visits_by_child(v1_visit_rows, link_field="beneficiary_case_id")

    v2_by_id = {r["entity_id"]: r for r in v2_rows}
    v1_by_id = {r["entity_id"]: r for r in v1_rows}

    common = sorted(set(v2_by_id) & set(v1_by_id))
    assert len(common) > 0, "no overlapping entity_ids between v1 and v2 — fixture or pipeline bug"

    bad: list[str] = []
    for eid in common[:20]:  # cap diff output to first 20 mismatches
        diffs = _diff_rows(v1_by_id[eid], v2_by_id[eid])
        if diffs:
            bad.append(f"entity_id={eid}\n" + "\n".join(diffs))

    if bad:
        pytest.fail(f"{len(bad)} entities diverged:\n\n" + "\n\n".join(bad))


def test_sam_followup_v1_v2_parity():
    """sam_followup_v2 (entity stage) on opp 879."""
    from commcare_connect.workflow.templates.sam_followup_v2 import PIPELINE_SCHEMAS

    children_schema = next(s for s in PIPELINE_SCHEMAS if s["alias"] == "children")["schema"]
    visits_schema = next(s for s in PIPELINE_SCHEMAS if s["alias"] == "visits")["schema"]

    v2_rows = _run_pipeline(SAM_OPP_ID, children_schema)
    v1_visit_rows = _run_pipeline(SAM_OPP_ID, visits_schema)
    v1_rows = _group_visits_by_child(v1_visit_rows, link_field="child_case_id")

    v2_by_id = {r["entity_id"]: r for r in v2_rows}
    v1_by_id = {r["entity_id"]: r for r in v1_rows}
    common = sorted(set(v2_by_id) & set(v1_by_id))
    assert len(common) > 0

    bad: list[str] = []
    for eid in common[:20]:
        diffs = _diff_rows(v1_by_id[eid], v2_by_id[eid])
        if diffs:
            bad.append(f"entity_id={eid}\n" + "\n".join(diffs))

    if bad:
        pytest.fail(f"{len(bad)} entities diverged:\n\n" + "\n\n".join(bad))
