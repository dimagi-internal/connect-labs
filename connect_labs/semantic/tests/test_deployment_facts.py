"""The two inputs the compiler needs and SQL cannot produce: `llo_map` and `settings`.

Both existed only inside kmc_programme_metrics_render.js. The compiler had supported
them since it was written and the tests exercised them with hand-built literals, so
every guard passed while the ONE caller that matters -- `semantic_indicators_api` --
passed neither. That combination is why this file exists: it asserts against the
shipped `deployment.yml`, not against a fixture written to agree with it.

Two distinct failures were live, and only one of them was loud:

  * `scopes=...,llo` raised RegistryError -> HTTP 400. Visible immediately.
  * `_suppression_columns` returns "" on falsy settings, so every response carried
    NO suppression columns. Mortality was published for LLOs the workbook says do
    not record deaths credibly, and it looked exactly like a real red band.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from connect_labs.semantic.compiler import RegistryError, compile_rollup_sql
from connect_labs.semantic.runtime import load_deployment

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"

ALL_SCOPES = ["programme", "opportunity", "llo", "flw", "month"]


@pytest.fixture(scope="module")
def props_doc():
    return yaml.safe_load((REGISTRY / "properties.yml").read_text())


@pytest.fixture(scope="module")
def registry():
    return yaml.safe_load((REGISTRY / "indicators.yml").read_text())


@pytest.fixture(scope="module")
def deployment():
    return load_deployment("kmc")


def test_deployment_facts_load(deployment):
    llo_map, settings = deployment
    assert llo_map, "deployment.yml must supply an llo_map; without it `llo` cannot compile"
    assert set(settings) == {
        "mortality_recording_credible",
        "completion_recording_credible",
    }, "every suppression rule in indicators.yml names one of these settings"


def test_every_suppression_rule_gates_an_indicator_the_registry_computes(registry):
    """A rule naming an indicator the registry does not compute is skipped by the
    compiler by design -- so a rule left behind by a rename gates nothing, silently."""
    computed = {m["meta"]["indicator"] for m in registry["measures"] if m.get("meta")}
    orphans = [r["indicator"] for r in registry.get("suppression") or [] if r["indicator"] not in computed]
    assert not orphans, f"suppression rules for indicators this registry does not compute: {orphans}"


def test_every_suppression_rule_has_a_setting_table(registry, deployment):
    """A rule whose table is missing is skipped SILENTLY -- the gate simply never fires."""
    _, settings = deployment
    declared = {r["setting"] for r in registry.get("suppression") or []}
    assert declared <= set(settings), f"suppression rules with no table in deployment.yml: {declared - set(settings)}"


def test_llo_map_covers_every_opportunity_the_registry_is_run_over(deployment):
    """An opportunity missing from the map lands in no LLO and vanishes from the drill."""
    llo_map, _ = deployment
    # The eleven live opportunities of the KMC programme, from the render's own
    # OPP_LABEL. A twelfth id added to the dashboard without a row here would drop
    # out of the `llo` scope without any error.
    for opp in (10021, 10019, 10015, 10022, 10018, 10014, 10020, 10017, 10016, 10013, 10042):
        assert opp in llo_map, f"opportunity {opp} has no LLO"


def test_the_registry_compiles_at_every_scope_with_the_shipped_facts(props_doc, registry, deployment):
    """The regression: this raised RegistryError, so the llo scope could not be served."""
    llo_map, settings = deployment
    sql = compile_rollup_sql(
        props_doc,
        registry,
        "SELECT 1",
        scopes=ALL_SCOPES,
        llo_map=llo_map,
        settings=settings,
    )
    assert "AS llo" in sql
    assert "cohort_month" in sql


def test_without_the_facts_the_llo_scope_does_not_compile(props_doc, registry):
    """Pinning the failure the wiring fixes, so a regression is loud rather than quiet."""
    with pytest.raises(RegistryError):
        compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=ALL_SCOPES)


def test_suppression_columns_are_actually_emitted(props_doc, registry, deployment):
    """The silent half. No settings -> no columns -> an ungated mortality figure."""
    llo_map, settings = deployment
    sql = compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=ALL_SCOPES, llo_map=llo_map, settings=settings)
    assert "mortality_suppressed" in sql, "the gate the workbook exists to enforce"
    assert "'PIPN'" in sql and "'EHA'" in sql, "the credible pair drives the NOT IN"

    without = compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=["programme"], settings=None)
    assert "mortality_suppressed" not in without, "the unwired endpoint emitted exactly this"


def test_completion_credibility_is_an_allow_list_not_a_deny_list(deployment):
    """The render's table meant the opposite of what the compiler reads.

    `COMPLETION_CREDIBLE = {GHI: false}` is a DENY-list: the render tests
    `COMPLETION_CREDIBLE[llo] !== false`, so every LLO except GHI is credible. The
    compiler builds `credible = [k for k, v in table.items() if v]` and suppresses
    everything outside it -- so that dict ported verbatim yields an EMPTY credible
    set and a completion gate TRUE for everyone, withholding completion from all six
    LLOs instead of one. Nothing on screen distinguishes the two. (No indicator reads
    this setting yet -- completion waits on its definition -- but the table is kept
    right for the day one does.)
    """
    _, settings = deployment
    completion = settings["completion_recording_credible"]
    credible = {k for k, v in completion.items() if v}
    assert "GHI" not in credible, "GHI is the one LLO the workbook excludes"
    assert credible == {
        "PIPN",
        "NAMA",
        "EHA",
        "Kikapu",
        "BERI",
    }, "every other LLO must be listed true explicitly; an absent LLO is suppressed"


def test_mortality_credibility_stays_the_workbook_pair(deployment):
    _, settings = deployment
    credible = {k for k, v in settings["mortality_recording_credible"].items() if v}
    assert credible == {"PIPN", "EHA"}, "the source doc: only PIPN and EHA record deaths credibly"


def test_bands_are_in_the_same_units_as_the_sql_that_produces_the_value(registry):
    """A band is graded against the value, so it must be in that value's units.

    The workbook's indicators were copied out of the render, where `evaluate` returns
    a RATIO (num/den) and `fmt` multiplies by 100 at display time. The registry's sql
    does the scaling itself -- `100.0 * {num} / NULLIF({den}, 0)` -- so the value is
    already a percentage, and the bands came across unconverted: one graded a value
    of 60.0 against a threshold of 0.6.

    Nothing failed. `nBandOf` does `x >= b[0]`, so 60.0 >= 0.6 and every percentage
    indicator in the series bands GREEN, at every scope, whatever the number. A 5%
    figure reads green. That is the exact failure `measure_catalog`'s docstring says
    serving the catalog alongside the rows exists to prevent -- "a band cannot drift
    from the measure it grades" -- and it was live in the shipped registry, unreached
    only because no client rendered those indicators yet.

    A genuine sub-1% threshold would trip this. That is intended: it should be an
    explicit decision, not a silent unit change.
    """
    offenders = []
    for m in registry["measures"]:
        meta = m.get("meta") or {}
        bands = meta.get("bands")
        if not bands or meta.get("unit") != "%":
            continue
        if "100.0 *" not in str(m.get("sql") or ""):
            continue
        flat = [x for v in bands for x in (v if isinstance(v, list) else [v])]
        if max(flat) <= 1.0:
            offenders.append((meta.get("indicator"), bands))
    assert not offenders, f"bands look like fractions but the value is a percentage: {offenders}"


def test_mortality_is_graded_on_the_percent_scale():
    """There were two mortality measures and they disagreed by exactly 100x."""
    reg = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    [mort] = [m for m in reg["measures"] if (m.get("meta") or {}).get("indicator") == "mortality"]
    assert mort["meta"]["bands"] == [[4, 12], [2, 16]]


# ── the monthly trend follows the drill ──────────────────────────────────────


def test_the_month_scope_has_a_drilled_counterpart_for_every_drill_level():
    """A bare `month` answers only the UNDRILLED trend.

    The dashboard's Monthly trend tab re-cohorts when you pick an LLO, an
    opportunity or a worker on the Indicators tab. `month` groups by cohort_month
    alone, so it cannot serve any of those — which is why the browser had to keep
    an indicator engine alive purely to compute drilled monthly series, and why a
    frozen run has to precompute `monthlyByScope` at freeze time.
    """
    from connect_labs.semantic.compiler import SCOPES

    for drill in ("llo", "opportunity", "flw"):
        composite = f"{drill}_month"
        assert composite in SCOPES, f"no {composite} scope; the drilled trend cannot be served"
        assert SCOPES[composite] == SCOPES[drill] + ["cohort_month"], (
            f"{composite} must be exactly {drill} plus the month column, "
            "or its rows will not line up with the drill they follow"
        )


def test_every_scope_has_a_distinct_column_set():
    """Rows are labelled back to their scope by GROUPING() per column.

    Two scopes with the same column set would produce identical GROUPING()
    patterns, so the CASE would label both rows as whichever arm came first and
    one scope's numbers would silently render as the other's.
    """
    from connect_labs.semantic.compiler import SCOPES

    seen: dict[tuple[str, ...], str] = {}
    for name, cols in SCOPES.items():
        key = tuple(sorted(cols))
        assert key not in seen, f"{name} and {seen[key]} share a column set {key}"
        seen[key] = name


def test_all_eight_scopes_compile_in_one_pass(props_doc, registry, deployment):
    """Three more GROUPING SETS in the SAME pass, not three more queries."""
    import re

    llo_map, settings = deployment
    sql = compile_rollup_sql(
        props_doc,
        registry,
        "SELECT 1",
        scopes=[
            "programme",
            "opportunity",
            "llo",
            "flw",
            "month",
            "llo_month",
            "opportunity_month",
            "flw_month",
        ],
        llo_map=llo_map,
        settings=settings,
    )
    assert sql.count("GROUPING SETS") == 1, "must stay a single pass"
    sets = re.search(r"GROUPING SETS \((.*)\)", sql, re.S).group(1)
    assert sets.count("(") == 8, f"expected 8 grouping sets, got {sets.count('(')}"
    for label in ("llo_month", "opportunity_month", "flw_month"):
        assert f"THEN '{label}'" in sql, f"{label} rows would be labelled 'other'"


# ── the catalog must carry what the render formats with ──────────────────────


def test_the_catalog_distinguishes_counts_from_means():
    """`unit` alone does not decide how a value is printed.

    The case counts are counts. Visits per case shares their unit ('n') and is a
    RATIO of a sum over a count; formatting a mean as an integer drops a real decimal
    and reads as a value rather than a bug.

    Every indicator's own measure is `type: number` — it divides two others — so the
    distinction lives on its numerator. But only when the indicator IS its numerator:
    a rate's numerator is a `count` too (it counts cases), and it is a percentage.
    """
    from connect_labs.semantic.runtime import load_registry, measure_catalog

    _, reg = load_registry("kmc")
    cat = {m["indicator"]: m for m in measure_catalog(reg)}

    assert {i: cat[i]["kind"] for i in cat if cat[i]["unit"] == "n"} == {
        "total_cases": "count",
        "registered_cases": "count",
        "started_cases": "count",
        "cumulative_svns_reached": "count",
        # sum / count, written out: not the indicator's own numerator
        "visits_per_case": None,
    }
    assert cat["mean_early_growth_rate"]["kind"] == "mean"
    for ratio in ("pct_growth_computable", "pct_healthy_growth", "weight_rounding_rate"):
        assert cat[ratio]["kind"] is None, f"{ratio} is a rate, not a {cat[ratio]['kind']}"


def test_the_catalog_carries_prominence():
    """The render groups headline indicators from this; without it all 24 read equal."""
    from connect_labs.semantic.runtime import load_registry, measure_catalog

    _, reg = load_registry("kmc")
    cat = {m["indicator"]: m for m in measure_catalog(reg)}
    assert cat["mortality"]["prominence"] == "Top"
    assert cat["pct_growth_computable"]["prominence"] == "Lower"
    assert all(m["prominence"] for m in cat.values()), "every indicator needs a prominence"


def test_filtering_to_a_series_keeps_the_availability_gates():
    """The gates are infrastructure, not part of any series.

    The reachability walk cannot find them: they carry no `meta`, so they are not
    roots, and no indicator's sql references them — they are read ALONGSIDE a value,
    not inside it. So a filtered request came back with no `anyrec_*` columns at all, and a
    caller had no way to tell "the app never asked this question" from "the answer
    is 0". A worker who logged no danger signs has not achieved a 0% danger-sign
    rate. That distinction was worth 268 of 5,302 per-FLW checks when it was ported.
    """
    from connect_labs.semantic.runtime import filter_to_series, load_registry, measure_catalog

    _, reg = load_registry("kmc")
    all_gates = {m["name"] for m in reg["measures"] if m.get("gate")}
    assert all_gates, "the registry must mark its gates explicitly, not by name prefix"

    for series, expected_indicators in (("KMC", 24),):
        kept = filter_to_series(reg, series)
        names = {m["name"] for m in kept["measures"]}
        assert all_gates <= names, f"series={series} dropped gates: {sorted(all_gates - names)}"
        # and keeping them must not smuggle them into the display contract
        assert len(measure_catalog(kept)) == expected_indicators


# ── the expression grammar ───────────────────────────────────────────────────


def _probe(props_doc, registry, fragment):
    """validate() a registry with one extra measure carrying `fragment`."""
    import copy

    from connect_labs.semantic.compiler import validate

    r = copy.deepcopy(registry)
    r["measures"].append({"name": "probe_x", "type": "number", "sql": fragment})
    return [p for p in validate(props_doc, r, llo_map={10042: "BERI"}) if p.startswith("probe_x")]


def test_the_shipped_registry_passes_its_own_grammar(props_doc, registry):
    """The allowlist has to describe the registry we actually have.

    First draft rejected 62 fragments in the shipped file — measure references read
    as brace structs, and it had no room for an array-indexed median or an
    ordered-set aggregate. A grammar that fails the corpus it governs is a grammar
    nobody can turn on.
    """
    from connect_labs.semantic.compiler import validate

    assert validate(props_doc, registry, llo_map={10042: "BERI"}) == []


def test_a_subquery_is_refused(props_doc, registry):
    """The hole this closes. `validate()` inspected only the {CUBE} references it
    could FIND, so a fragment containing none passed untouched — and a measure's sql
    is interpolated RAW into the compiled query."""
    assert _probe(props_doc, registry, "(SELECT 1)")
    problems = _probe(props_doc, registry, "(SELECT count(*) FROM auth_user)")
    assert problems and "subquery" in problems[0]


def test_an_unlisted_function_is_refused(props_doc, registry):
    problems = _probe(props_doc, registry, "pg_read_file('/etc/passwd')")
    assert problems and "pg_read_file" in problems[0]


def test_legitimate_expressions_still_pass(props_doc, registry):
    """An allowlist that rejects real measures is worse than none — it gets removed."""
    for fragment in (
        "100.0 * {CUBE}.registered",
        "CASE WHEN {CUBE}.registered THEN 1 ELSE 0 END",
        "COALESCE({CUBE}.n_weights, 0) > 0",
        "100.0 * {pct_healthy_growth_numerator} / NULLIF({pct_healthy_growth_denominator}, 0)",
    ):
        assert _probe(props_doc, registry, fragment) == [], fragment


def test_an_unknown_column_is_still_caught(props_doc, registry):
    """The pre-existing check must survive the new one."""
    problems = _probe(props_doc, registry, "{CUBE}.not_a_real_column")
    assert problems and "unknown column" in problems[0]


def test_every_cohort_source_and_one_clone_of_it_is_mapped_to_its_llo():
    """The seed's LLO map covers the synthetic KMC cohort: each source opportunity,
    and one labs clone per source with the same organisation mix.

    The clone ids are allocated at generation time, so they cannot be listed in the
    cohort file; what CAN be checked is that the map carries as many clones as the
    cohort has sources, in the same organisations. 2166 joined the cohort on
    2026-09-10 and its clone 10062 reached the live synthetic registry but not this
    seed -- so a KMC workflow created from the template, which computes from this
    seed until it is bound to a record, would have left 10062's babies with no LLO.
    """
    from collections import Counter
    from pathlib import Path

    import yaml

    import connect_labs.labs.synthetic as synthetic_pkg

    cohort = yaml.safe_load((Path(synthetic_pkg.__file__).parent / "cohorts" / "kmc.yaml").read_text())
    sources = [int(o) for o in cohort["opportunity_ids"]]
    seed = yaml.safe_load((Path(__file__).resolve().parents[1] / "registry" / "kmc" / "deployment.yml").read_text())
    llo_map = {int(k): v for k, v in seed["llo_map"].items()}

    missing = [o for o in sources if o not in llo_map]
    assert not missing, f"cohort sources with no LLO in the seed: {missing}"
    source_mix = Counter(llo_map[o] for o in sources)
    clone_mix = Counter(v for k, v in llo_map.items() if k >= 10000)
    assert clone_mix == source_mix, f"clones {dict(clone_mix)} vs sources {dict(source_mix)}"
