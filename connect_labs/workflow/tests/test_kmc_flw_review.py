"""KMC Worker Review: the programme drill's second workflow.

The programme page must stop at the worker or become the everything-page; this
is where a worker row opens. These pin what makes it safe to link to: it computes
nothing (it reads the programme report's own payload for that worker), it is
linked by configuration, and it stays in the browser dialect the runner needs.
"""

import re
from pathlib import Path

from connect_labs.workflow.templates import TEMPLATES
from connect_labs.workflow.templates.kmc_flw_review import DEFINITION, TEMPLATE
from connect_labs.workflow.templates.kmc_programme_metrics import DEFINITION as METRICS_DEFINITION

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
RENDER = TEMPLATES_DIR / "kmc_flw_review_render.js"
METRICS_RENDER = TEMPLATES_DIR / "kmc_programme_metrics_render.js"


def test_the_template_is_registered_as_a_multi_opp_drill_view():
    assert TEMPLATES["kmc_flw_review"] is TEMPLATE
    assert DEFINITION["config"]["renderWhileLoading"] is True, "the page must not sit behind the pipeline stream"
    assert "pipelinesLoaded" in RENDER.read_text(), "opting in means tolerating absent pipelines"
    assert TEMPLATE["multi_opp"] is True
    assert TEMPLATE["supports_saved_runs"] is False, "a drill view has no moment of completion"
    assert [p["alias"] for p in TEMPLATE["pipeline_schemas"]] == ["children", "visits"]


def test_the_render_reads_the_programme_report_and_grades_nothing():
    """One path. The worker's figures are the programme report's own rows, read
    through the preview endpoint the programme page reads; each case's
    contributions are the same registry at the `case` scope, filtered to the
    worker, as of the report -- read from the semantic endpoint. Never a JavaScript
    grader."""
    src = RENDER.read_text()
    assert "/snapshot/preview/" in src
    assert "source_run" in src
    assert "/runs/history/" in src, "opened on its own, it reads the newest saved report"
    assert "scopes=case&flw=" in src, "case contributions come from the case scope for this worker"
    assert "&as_of=" in src, "as of the report's date, not today"
    for gone in (
        "function cEntry(",
        "function bandOf(",
        "function nBandOf(",
        "_suppressed",
        "series=N&scopes=programme",
    ):
        assert gone not in src, f"the worker review must not carry {gone!r}"
    assert "P.byFLW" in src and "P.series" in src


def test_the_scorecard_shares_the_programme_reports_header_and_the_indicators_table_is_gone():
    """Same fifteen columns, same groups, same order as the programme page, with
    the worker's row under the programme, organisation and opportunity rows; the
    C-series table that used to sit beside the cases is gone (too cluttered)."""
    src = RENDER.read_text()
    assert "SCORECARD_GROUPS" in src and "function ScorecardHead" in src
    assert ">Indicators<" not in src and "Indicators\n" not in src.split("Scorecard")[0]
    block = src[src.index("var SCORECARD = [") : src.index("];", src.index("var SCORECARD = ["))]
    ids = re.findall(r"id: '(N\d\d)'", block)
    assert ids == [
        "N01",
        "N02",
        "N03",
        "N05",
        "N06",
        "N07",
        "N08",
        "N09",
        "N09",
        "N10",
        "N11",
        "N12",
        "N13",
        "N14",
        "N15",
    ]
    # case-level labels drop the aggregate words
    assert "caseLabel: 'GA'" in block and "caseLabel: 'BW'" in block
    assert "scorecardRow('Programme'" in src


def test_each_case_row_carries_its_contribution_and_opens_inline():
    """A rate becomes ✓/✗ (in the denominator and whether it counted), a median
    the case's own value, the growth class a dot in its column; a clicked case opens
    directly under its row."""
    src = RENDER.read_text()
    assert "function contrib(" in src
    assert "_denominator'" in src, "a contribution reads the measure's denominator at case scope"
    assert "forCases={true}" in src
    assert "'|detail'" in src, "the case view renders as a row under the case"
    assert "<CaseDetail c={c} />" in src


def test_the_render_stays_in_the_es5_dialect_the_runner_transpiles():
    src = RENDER.read_text()
    offenders = {
        "arrow function": re.findall(r"=>", src),
        "array destructuring": re.findall(r"var\s*\[", src),
        "computed property key": re.findall(r"\{\s*\[\w", src),
    }
    bad = {k: len(v) for k, v in offenders.items() if v}
    assert not bad, f"non-ES5 syntax in the worker review render: {bad}"


def test_cases_get_a_growth_chart_and_the_audit_is_scoped_to_the_worker():
    src = RENDER.read_text()
    assert "function GrowthChart" in src
    assert "15 g/kg/day" in src, "the reference line is the C13 target"
    assert "postmenstrual age" in src, "with gestational age known, the axis a preterm standard uses"
    assert "expected loss" in src, "a first-week loss is explained, not painted red"
    flat = re.sub(r"\s+", "", src)
    assert "actions.createAudit(" in flat
    assert "selected_flw_user_ids" in src and "granularity: 'per_flw'" in src
    assert "audit_recent_days" in src, "the audit window is the worker's recent work"


def test_linking_is_configuration_on_the_programme_workflow():
    """The programme render builds the link from `config.flw_review`; nothing
    about the review workflow's id lives in code."""
    assert "flw_review" in METRICS_DEFINITION["config"]
    assert METRICS_DEFINITION["config"]["flw_review"] is None
    src = METRICS_RENDER.read_text()
    assert "cfgAudit.flw_review" in src
    assert "source_run=" in src and "&flw=" in src
    assert DEFINITION["config"]["source_workflow_id"] is None
    assert "config.audit_recent_days" not in src, "the programme page does not own the review's settings"


def test_the_audit_routing_is_the_programme_pages_routing():
    """Two workflows, one hardware map: the review's scale-reader routing is
    derived from the same source as the programme page's."""
    for key in ("scale_agent_by_llo", "scale_unverified_llos", "weight_image_path", "weight_value_path"):
        assert DEFINITION["config"][key] == METRICS_DEFINITION["config"][key], key


def test_photos_come_through_the_frameworks_visit_image_route():
    """No pipeline change and no new endpoint: the visit rows carry the visit id,
    the workflow visit-images API returns each visit's blob ids, and the audit
    image route serves them -- the same path the audit review pages use."""
    src = RENDER.read_text()
    assert "/visit-images/" in src and "visit_ids=" in src
    assert "/audit/image/" in src
    assert "weighings photographed" in src


def test_a_worker_and_a_case_are_both_addressable():
    """A demo or a review note links straight to one baby: `?flw=` picks the
    worker, `?case=<entity_id>` opens the case once the cases are known, and the
    URL follows the selection so the address bar is always shareable."""
    src = RENDER.read_text()
    assert "qp('flw')" in src and "qp('case')" in src
    assert "searchParams.set('case'" in src
    assert "setSelCase(c)" not in src.replace(
        "function openCase(c) {\n    setSelCase(c);", ""
    ), "case selection must go through openCase so the URL follows it"
