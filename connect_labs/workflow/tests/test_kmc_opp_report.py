"""One opportunity's own report: its figures, its workers, its anonymous peers."""

from pathlib import Path

from connect_labs.workflow.templates import TEMPLATE_GROUP_OF, TEMPLATES
from connect_labs.workflow.templates.kmc_opp_report import DEFINITION, TEMPLATE
from connect_labs.workflow.templates.kmc_programme_metrics import TEMPLATE as PROGRAMME

RENDER = Path(__file__).resolve().parents[1] / "templates" / "kmc_opp_report_render.js"


def test_the_template_is_registered_as_a_single_opp_drill_view():
    assert TEMPLATES["kmc_opp_report"] is TEMPLATE
    assert TEMPLATE["supports_saved_runs"] is False, "a drill view has no moment of completion"
    assert DEFINITION["config"]["templateType"] == "kmc_opp_report"


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


def test_it_reads_its_own_figures_and_its_own_flws_from_the_semantic_layer():
    src = RENDER.read_text()
    assert "/semantic/" in src
    assert "scopes=opportunity,flw" in src or "scopes=flw" in src


def test_it_reads_the_benchmark_from_the_benchmarks_api():
    src = RENDER.read_text()
    assert "/labs/benchmarks/api/" in src


def test_the_flw_table_never_goes_through_the_benchmark_store():
    """An opportunity owns its workers' data, so the FLW table shows real
    usernames read directly. FLW identity must never cross an opportunity
    boundary, which is what the benchmark store is for."""
    src = RENDER.read_text()
    benchmark_chunks = src.split("/labs/benchmarks/api/")[1:]
    for chunk in benchmark_chunks:
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
    assert "withheld" in src and "cohort" in src, "the message must name both innocent causes"
    assert "if (!ids.length)" in src, "no branch for a payload carrying no cohort"
    empty_branch = src.split("if (!ids.length)")[1][:200]
    assert "benchmarkEmptyMessage" in empty_branch
    assert "status: 'error'" not in empty_branch
