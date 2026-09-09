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
    assert TEMPLATE["multi_opp"] is True
    assert TEMPLATE["supports_saved_runs"] is False, "a drill view has no moment of completion"
    assert [p["alias"] for p in TEMPLATE["pipeline_schemas"]] == ["children", "visits"]


def test_the_render_reads_the_programme_report_and_grades_nothing():
    """One path. The worker's figures are the programme report's own rows, read
    through the preview endpoint the programme page reads -- never a second
    evaluation, never a JavaScript grader."""
    src = RENDER.read_text()
    assert "/snapshot/preview/" in src
    assert "source_run" in src
    assert "/runs/history/" in src, "opened on its own, it reads the newest saved report"
    for gone in ("function cEntry(", "function bandOf(", "/semantic/", "_suppressed"):
        assert gone not in src, f"the worker review must not carry {gone!r}"
    assert "P.byFLW" in src and "P.series" in src


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
