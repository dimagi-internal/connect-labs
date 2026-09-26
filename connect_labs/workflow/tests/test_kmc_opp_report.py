"""One opportunity's own report: its figures, its workers, its anonymous peers."""

from pathlib import Path

from connect_labs.workflow.templates import TEMPLATE_GROUP_OF, TEMPLATES
from connect_labs.workflow.templates.kmc_opp_report import DEFINITION, TEMPLATE
from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS as PROGRAMME_SNAPSHOT_INPUTS
from connect_labs.workflow.templates.kmc_programme_metrics import TEMPLATE as PROGRAMME

RENDER = Path(__file__).resolve().parents[1] / "templates" / "kmc_opp_report_render.js"


def test_the_template_is_registered_as_a_saved_runs_report():
    assert TEMPLATES["kmc_opp_report"] is TEMPLATE
    assert TEMPLATE["supports_saved_runs"] is True
    assert TEMPLATE["receives_hand_down"] is True, "the programme report's weeks could not reach it"
    assert DEFINITION["config"]["templateType"] == "kmc_opp_report"


def test_a_saved_run_is_graded_exactly_like_the_programme_reports():
    """A handed-down slice and a run saved here must be the same shape, or the
    page reads the two differently. Same builder, same spec -- a copy, so an
    edit to one is a deliberate edit to both."""
    assert TEMPLATE["snapshot_inputs"] == PROGRAMME_SNAPSHOT_INPUTS
    assert TEMPLATE["snapshot_inputs"] is not PROGRAMME_SNAPSHOT_INPUTS
    assert DEFINITION["snapshot_inputs"] is TEMPLATE["snapshot_inputs"]
    assert PROGRAMME["hands_down_to_opportunity_reports"] is True


def test_its_worker_table_uses_the_programmes_scorecard_columns():
    from connect_labs.workflow.templates.kmc_programme_metrics import SCORECARD_COLUMNS, SCORECARD_GROUPS

    assert DEFINITION["config"]["scorecard_columns"] is SCORECARD_COLUMNS
    assert DEFINITION["config"]["scorecard_groups"] is SCORECARD_GROUPS
    assert sum(g["span"] for g in SCORECARD_GROUPS) == len(SCORECARD_COLUMNS)


def test_the_programme_renders_scorecard_is_the_same_column_list():
    """The programme render still holds its own copy of the columns in JS. Until
    it reads them from config too, this keeps the two from drifting: same ids,
    same labels, same order."""
    import re

    from connect_labs.workflow.templates.kmc_programme_metrics import SCORECARD_COLUMNS

    js = (RENDER.parent / "kmc_programme_metrics_render.js").read_text()
    block = js[js.index("var SCORECARD = [") : js.index("];", js.index("var SCORECARD = ["))]
    ids = re.findall(r"id: '([a-z0-9_]+)'", block)
    labels = re.findall(r"label: '([^']+)'", block)
    assert ids == [c["id"] for c in SCORECARD_COLUMNS]
    assert [bytes(x, "utf-8").decode("unicode_escape") for x in labels] == [c["label"] for c in SCORECARD_COLUMNS]


def test_it_is_a_single_opportunity_page_that_fetches_its_own_data():
    """`multi_opp` is the difference between this and the programme report, and
    the two render flags are what let the page paint before any pipeline stream."""
    assert TEMPLATE["multi_opp"] is False
    assert DEFINITION["config"]["multi_opp"] is False
    assert DEFINITION["config"]["renderWhileLoading"] is True
    assert DEFINITION["config"]["noPipelineStream"] is True


def test_it_shares_the_programme_reports_pipeline_aliases():
    """An instance can be pointed at the programme report's own pipeline records
    (`home_scope`), which only works while both name the same two aliases."""
    assert [s["alias"] for s in TEMPLATE["pipeline_schemas"]] == [s["alias"] for s in PROGRAMME["pipeline_schemas"]]
    for mine, theirs in zip(TEMPLATE["pipeline_schemas"], PROGRAMME["pipeline_schemas"]):
        assert mine["schema"] is theirs["schema"], "the schema must be the programme report's, not a copy"


def test_it_is_placed_in_the_picker():
    assert TEMPLATE_GROUP_OF["kmc_opp_report"] == "reports"


def test_it_resolves_its_definition_id_instead_of_trusting_the_prop():
    """The `definition` prop is the record's data blob and never carries `id`;
    trusting it sent every request to /api/undefined/ in production."""
    src = RENDER.read_text()
    assert "function definitionId()" in src
    assert "instance && instance.definition_id" in src
    for chunk in src.split("'/labs/workflow/api/'")[1:]:
        assert "definition.id" not in chunk[:120], "bare prop id in a fetch URL"


def test_no_fetch_builds_its_url_from_the_definition_prop():
    """Stronger than the /labs/workflow/api/ check above: whatever a future URL
    looks like, `definition.id` must not be what addresses it. Mutating any
    fetch back to the prop reddens this."""
    src = RENDER.read_text()
    for chunk in src.split("fetch(")[1:]:
        assert "definition.id" not in chunk[:240], "a fetch addressed by the definition prop"


def test_it_reads_a_saved_run_off_the_run_and_a_live_one_as_a_preview():
    """One payload, as on the programme report: a completed run's stored snapshot,
    or the same builder's preview for a run still in progress."""
    src = RENDER.read_text()
    assert "view.state.snapshot" in src
    assert "/snapshot/preview/" in src
    assert "/semantic/" not in src, "the page must not compute its own figures any more"


def test_the_render_grades_nothing():
    """Every cell arrives graded by the server, credibility included
    (semantic/snapshot.py). A second grader in the browser is how the two used to
    disagree."""
    src = RENDER.read_text()
    for marker in ("function gradeCell(", "function bandOf(", "_suppressed"):
        assert marker not in src, marker


def test_it_reads_the_benchmark_from_the_benchmarks_api():
    src = RENDER.read_text()
    assert "/labs/benchmarks/api/" in src


def _flw_rows_body():
    """Just the worker rows (the `workerRows` memo and the sort applied to it),
    so an assertion about where the worker table's data comes from cannot be
    satisfied by some unrelated part of the file."""
    src = RENDER.read_text()
    start = src.index("var workerRows = React.useMemo(")
    end = src.index("\n  // ══ Headline tiles", start)
    return src[start:end]


def test_the_flw_table_never_goes_through_the_benchmark_store():
    """An opportunity owns its workers' data, so the FLW table shows real
    usernames read directly. FLW identity must never cross an opportunity
    boundary, which is what the benchmark store is for.

    This asserts about the TABLE, not about a URL. The previous version split on
    the benchmark fetch URL and checked the next 200 characters for 'flw' -- a
    window that never reached the memo sixty lines above it, so routing a
    username through the benchmark payload (`props.bench && props.bench.
    flw_username`) left all fifteen tests green. Verified by re-running that
    exact mutation against this version: it goes red.
    """
    body = _flw_rows_body()
    assert "P.byFLW" in body, "the worker table no longer sources from this opportunity's snapshot"
    assert "bench" not in body, "the worker table reads the benchmark payload"
    # And the benchmark fetch itself still asks for no worker-level data.
    src = RENDER.read_text()
    for chunk in src.split("/labs/benchmarks/api/")[1:]:
        assert "flw" not in chunk[:200].lower(), "the benchmark fetch mentions flw"


def test_a_failed_fetch_is_not_rendered_as_absent_data():
    """The failure mode that made an earlier KMC page read as 'this worker has
    no weighings' when the request had 404'd."""
    src = RENDER.read_text()
    assert "r.ok ? r.json() : { rows: [] }" not in src
    assert "status: 'error'" in src


def test_every_fetch_has_an_error_state_of_its_own():
    """One `status: 'error'` anywhere in the file satisfies the test above while
    a second fetch quietly maps its failure to empty. So look per fetch: the
    text following each `fetch(` up to the next one has to set an error state
    of its own. Deleting EITHER handler reddens this; a count-based assertion
    did not (verified by mutation)."""
    src = RENDER.read_text()
    chunks = src.split("fetch(")[1:]
    assert len(chunks) >= 2, "expected the semantic read and the benchmark read"
    for i, chunk in enumerate(chunks):
        assert "status: 'error'" in chunk, f"fetch #{i + 1} maps its failure to something other than an error"


def test_an_empty_benchmark_is_explained_rather_than_errored():
    """No cohort yet, or the disclosure rules withheld everything, is a normal
    state: the page says so in words and must not reach the error branch.

    Asserting the message VARIABLE exists is not enough — renaming its
    declaration left every use site intact and the test stayed green. So this
    pins the BRANCH: no cohorts came back, therefore render the explanation,
    and nothing about an error."""
    src = RENDER.read_text()
    assert "benchmarkEmptyMessage" in src
    assert "cohort" in src and "republished" in src, "the message must name both innocent causes"
    assert "if (!built.cid)" in src, "no branch for a payload carrying no organisation benchmark"
    empty_branch = src.split("if (!built.cid)")[1][:200]
    assert "benchmarkEmptyMessage" in empty_branch
    assert "status: 'error'" not in empty_branch


def test_the_benchmark_tab_compares_organisations_from_the_benchmark_store():
    """Organisations, not opportunities: a stable peer set, read from the store's
    organisation rows -- never from the report's own snapshot, which carries only
    this opportunity."""
    src = RENDER.read_text()
    body = src[src.index("function benchmarkRows(") : src.index("  // ══ The worker table")]
    assert "e.organisations" in body
    assert "R.rankOrganisations(" in body and "<R.MiniRankBars" in body and "<R.RankedBars" in body
    assert "P.byLLO" not in body and "P.byFLW" not in body


def test_the_peer_trend_lines_are_gone():
    """Anonymous peer lines over tenure were unreadable; the tab is one scorecard."""
    src = RENDER.read_text()
    assert "PeerTrend" not in src and "PeerCard" not in src


def test_the_other_organisations_are_never_named_on_the_page():
    src = RENDER.read_text()
    body = src[src.index("function benchmarkRows(") : src.index("  // ══ The worker table")]
    assert "Another organisation" in body
    assert ".organisation" not in body.replace(".organisations", ""), "a provenance field reached the render"


def test_there_is_one_indicator_set_and_no_series_switch():
    """The Scorecard (N) / Workbook (C) toggle chose between two families. There
    is one now (#2004): no switch, no `series=` on the fetch, and peers are looked
    up under each measure's own family rather than a letter from the URL."""
    src = RENDER.read_text()
    assert "SeriesSwitch" not in src and "Workbook (C)" not in src
    assert "'series='" not in src and "qp('series')" not in src
    assert "(fam[m.series] || {})[m.indicator]" in src
