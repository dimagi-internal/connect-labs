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
    assert "P.byFLW" in src and "P.cMeasures" in src


def test_the_scorecard_shares_the_programme_reports_header_and_the_indicators_table_is_gone():
    """Same fifteen columns, same groups, same order as the programme page, with
    the worker's row under the programme, organisation and opportunity rows; the
    full indicator table that used to sit beside the cases is gone (too cluttered)."""
    src = RENDER.read_text()
    block = src[src.index("var SCORECARD = [") : src.index("];", src.index("var SCORECARD = ["))]
    ids = re.findall(r"id: '([a-z0-9_]+)'", block)
    assert ids == [
        "total_cases",
        "registered_cases",
        "started_cases",
        "median_gestational_age",
        "median_birthweight",
        "visits_per_case",
        "pct_enrolled_within_3d",
        "pct_growth_sufficient",
        "pct_growth_sufficient",
        "pct_healthy_growth",
        "pct_slow_growth",
        "pct_fast_growth",
        "mortality",
        "weight_rounding_rate",
        "pct_impossible_weight_changes",
    ]


def test_each_case_row_reads_the_measures_case_scope_denominator():
    """A rate becomes ✓/✗ from whether the case is in the measure's denominator."""
    src = RENDER.read_text()
    assert "_denominator'" in src, "a contribution reads the measure's denominator at case scope"


def test_the_render_stays_in_the_es5_dialect_the_runner_transpiles():
    src = RENDER.read_text()
    offenders = {
        "arrow function": re.findall(r"=>", src),
        "array destructuring": re.findall(r"var\s*\[", src),
        "computed property key": re.findall(r"\{\s*\[\w", src),
    }
    bad = {k: len(v) for k, v in offenders.items() if v}
    assert not bad, f"non-ES5 syntax in the worker review render: {bad}"


def test_the_audit_is_scoped_to_the_worker():
    src = RENDER.read_text()
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


def test_a_worker_and_a_case_are_both_addressable():
    """A demo or a review note links straight to one baby: `?flw=` picks the
    worker, `?case=<entity_id>` opens the case once the cases are known, and the
    URL follows the selection so the address bar is always shareable."""
    src = RENDER.read_text()
    assert "qp('flw')" in src and "qp('case')" in src
    assert "searchParams.set('case'" in src


def test_the_programme_report_creates_this_workflow_as_its_companion():
    """One "Create" gives the whole drill. The programme template declares this
    one as a companion sharing its two pipelines, with its run minted and both
    sides of the link written — so a report created BY HAND on a real programme
    links from its first render, and the four-call runbook is gone."""
    from connect_labs.workflow.templates.kmc_programme_metrics import TEMPLATE as METRICS_TEMPLATE

    assert METRICS_TEMPLATE["companions"] == [
        {
            "template_key": "kmc_flw_review",
            "config_key": "flw_review",
            "share_pipelines": True,
            "mint_run": True,
            "back_reference": "source_workflow_id",
        }
    ]
    # Written at create time, never a real id in the template: an id here would
    # point every new report at one review workflow in one scope.
    assert METRICS_DEFINITION["config"]["flw_review"] is None
    assert DEFINITION["config"]["source_workflow_id"] is None
    # The render reads exactly the shape the companion mechanism writes.
    src = METRICS_RENDER.read_text()
    assert "FLW_REVIEW.workflow_id" in src and "FLW_REVIEW.run_id" in src
    assert "cfgAudit.flw_review" in src


def test_the_review_fetches_its_own_rows_instead_of_the_cohort_stream():
    """The page needs one worker's cases and one case's weighings. The framework
    default handed it every pipeline's rows for all twelve opportunities (~30 MB)
    to filter in the browser -- ~20s warm, minutes cold, and the case panel waited
    on it. It now asks for what it needs, and opts out of the stream."""
    from connect_labs.workflow.templates.kmc_flw_review import TEMPLATE

    src = TEMPLATE["render_code"]
    assert TEMPLATE["definition"]["config"]["noPipelineStream"] is True
    assert "/pipeline-rows/" in src, "the review does not fetch its own rows"
    assert "alias=children" in src and "alias=visits" in src
    assert "pipelines.children" not in src, "still reading the streamed cohort rows"
    assert "pipelines.visits" not in src


def test_the_runner_honours_the_opt_out():
    from pathlib import Path

    runner = (Path(__file__).resolve().parents[2] / "static" / "js" / "workflow-runner.tsx").read_text()
    assert "noPipelineStream" in runner


def test_the_row_fetches_resolve_the_definition_id_instead_of_trusting_the_prop():
    """Both `pipeline-rows` fetches must build their URL from a resolver, not from
    a bare `definition.id`.

    The `definition` prop is the record's `data` blob; the record's pk travels
    beside it as `definition_id` and is never written into `data`. So
    `definition.id` was always undefined and both fetches went to
    `/labs/workflow/api/undefined/pipeline-rows/` -> 404 for every user, emptying
    the case table's live columns and every weight series. The programme page
    already resolves this (see its `definitionId()`); this is the same chain.
    """
    src = RENDER.read_text()
    # The keys the record's pk actually travels under.
    assert "definition.definition_id" in src
    assert "instance && instance.definition_id" in src
    # No fetch may interpolate the prop's id straight into an api path.
    for m in re.finditer(r"'/labs/workflow/api/' \+\s*([^\n]+)", src):
        assert "definition.id" not in m.group(1), f"bare prop id in a fetch URL: {m.group(1).strip()!r}"


def _legacy_block(path):
    src = path.read_text()
    start = src.index("  var LEGACY_ID = {")
    end = src.index("  function fromLegacyIds(p) {")
    body_end = src.index("\n  }\n", src.index("    return Object.assign({}, p, {", end)) + 4
    return src[start:end] + src[end:body_end]


def test_it_reads_a_pre_2004_run_through_the_programme_reports_own_translation():
    """This page reads the programme report's SAVED runs, and a run saved before
    the indicator set was unified carries C/N codes. Renders cannot import one
    another, so the translation is a copy -- and two copies that drift would show
    one run's figures under different names on the two pages."""
    programme = RENDER.parent / "kmc_programme_metrics_render.js"
    assert "fromLegacyIds(report.payload" in RENDER.read_text()
    assert _legacy_block(RENDER) == _legacy_block(programme)


def test_the_programme_row_reads_the_programme_cells():
    """The scorecard's first row is the programme. When the scorecard stopped
    reading `P.series.N`, the object it reads lost its `programme` key and the
    row rendered blank on every worker."""
    src = RENDER.read_text()
    block = src[src.index("  var SC = {") : src.index("};", src.index("  var SC = {"))]
    assert "programme: P.programInd" in block


def test_a_cold_case_cache_is_an_error_with_a_retry_not_a_blank_table():
    """A cold visit cache answers 200 with rows missing. Treated as ready, the
    table read as a worker whose cases counted for nothing."""
    src = RENDER.read_text()
    body = src[src.index("could not read case contributions") :]
    body = body[: body.index("setCaseRows({ status: 'ready'")]
    assert "cold_cache" in body and "partial_cache" in body and "throw new Error" in body
