"""The two inputs the compiler needs and SQL cannot produce: `llo_map` and `settings`.

Both existed only inside kmc_programme_metrics_render.js. The compiler had supported
them since it was written and the tests exercised them with hand-built literals, so
every guard passed while the ONE caller that matters -- `semantic_indicators_api` --
passed neither. That combination is why this file exists: it asserts against the
shipped `deployment.yml`, not against a fixture written to agree with it.

Two distinct failures were live, and only one of them was loud:

  * `scopes=...,llo` raised RegistryError -> HTTP 400. Visible immediately.
  * `_suppression_columns` returns "" on falsy settings, so every C-series response
    carried NO suppression columns. C14 published a mortality figure for LLOs the
    workbook says do not record deaths credibly, and it looked exactly like a real
    red band.
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
    return load_deployment()


def test_deployment_facts_load(deployment):
    llo_map, settings = deployment
    assert llo_map, "deployment.yml must supply an llo_map; without it `llo` cannot compile"
    assert set(settings) == {
        "mortality_recording_credible",
        "completion_recording_credible",
    }, "every suppression rule in indicators.yml names one of these settings"


def test_the_registry_computes_exactly_the_indicators_the_render_did(registry):
    """22, the same 22. The swap is 1:1, so deleting the JS engine loses nothing.

    The other eleven (C03, C04, C18, C22, C25-C27, C29, C30, C32, C33) are declared
    by the workbook and computed by NEITHER engine; the render lists them under
    NOT_COMPUTABLE and this registry simply has no measure for them.
    """
    computed = {
        m["meta"]["indicator"]
        for m in registry["measures"]
        if m.get("meta") and str(m["meta"].get("indicator", "")).startswith("C")
    }
    assert computed == {
        "C01",
        "C02",
        "C05",
        "C06",
        "C07",
        "C08",
        "C09",
        "C10",
        "C11",
        "C12",
        "C13",
        "C14",
        "C15",
        "C16",
        "C17",
        "C19",
        "C20",
        "C21",
        "C23",
        "C24",
        "C28",
        "C31",
    }


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


def test_the_c_series_compiles_at_every_scope_with_the_shipped_facts(props_doc, registry, deployment):
    """The regression: this raised RegistryError, so `series=C` could not be served."""
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


def test_without_the_facts_the_c_series_does_not_compile(props_doc, registry):
    """Pinning the failure the wiring fixes, so a regression is loud rather than quiet."""
    with pytest.raises(RegistryError):
        compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=ALL_SCOPES)


def test_suppression_columns_are_actually_emitted(props_doc, registry, deployment):
    """The silent half. No settings -> no columns -> an ungated mortality figure."""
    llo_map, settings = deployment
    sql = compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=ALL_SCOPES, llo_map=llo_map, settings=settings)
    assert "c14_suppressed" in sql, "the gate the workbook exists to enforce"
    assert "'PIPN'" in sql and "'EHA'" in sql, "the credible pair drives the NOT IN"

    # C18 declares a rule but the registry does not COMPUTE C18 -- it is one of the
    # eleven the workbook declares and neither engine derives -- so the compiler
    # skips it by design. Asserting its column would pin a bug, not a behaviour.
    assert "c18_suppressed" not in sql

    without = compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=["programme"], settings=None)
    assert "c14_suppressed" not in without, "the unwired endpoint emitted exactly this"


def test_completion_credibility_is_an_allow_list_not_a_deny_list(deployment):
    """The render's table meant the opposite of what the compiler reads.

    `COMPLETION_CREDIBLE = {GHI: false}` is a DENY-list: the render tests
    `COMPLETION_CREDIBLE[llo] !== false`, so every LLO except GHI is credible. The
    compiler builds `credible = [k for k, v in table.items() if v]` and suppresses
    everything outside it -- so that dict ported verbatim yields an EMPTY credible
    set and `TRUE AS c18_suppressed`, withholding C18 from all six LLOs instead of
    one. Nothing on screen distinguishes the two.
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

    The C-series was copied out of the render, where `evaluate` returns a RATIO
    (num/den) and `fmt` multiplies by 100 at display time. The registry's sql does
    the scaling itself -- `100.0 * {num} / NULLIF({den}, 0)` -- so the value is
    already a percentage, and the bands came across unconverted: C09 graded a value
    of 60.0 against a threshold of 0.6.

    Nothing failed. `nBandOf` does `x >= b[0]`, so 60.0 >= 0.6 and every percentage
    indicator in the series bands GREEN, at every scope, whatever the number. A 5%
    figure reads green. That is the exact failure `measure_catalog`'s docstring says
    serving the catalog alongside the rows exists to prevent -- "a band cannot drift
    from the measure it grades" -- and it was live in the shipped registry, unreached
    only because no client rendered the C-series yet.

    The N-series, which IS rendered, had it right: N13 and C14 are the same mortality
    measure and N13's bands were exactly 100x C14's.

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


def test_the_two_mortality_measures_agree_on_their_band():
    """C14 and N13 are the same measure. They disagreed by exactly 100x."""
    reg = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    bands = {
        m["meta"]["indicator"]: m["meta"].get("bands")
        for m in reg["measures"]
        if (m.get("meta") or {}).get("indicator") in ("C14", "N13")
    }
    assert bands["C14"] == bands["N13"] == [[4, 12], [2, 16]]


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

    C01, C02 and C05 are counts. C06 and C24 share their unit ('n') and are MEANS.
    The render's old `IND` said so via `kind`; formatting a mean as an integer drops
    a real decimal and reads as a value rather than a bug.

    Every indicator's own measure is `type: number` — it divides two others — so the
    distinction lives on its numerator. But only when the indicator IS its numerator:
    C09's numerator is a `count` too (it counts cases), and C09 is a percentage.
    """
    from connect_labs.semantic.runtime import filter_to_series, load_registry, measure_catalog

    _, reg = load_registry()
    cat = {m["indicator"]: m for m in measure_catalog(filter_to_series(reg, "C"))}

    assert {i: cat[i]["kind"] for i in cat if cat[i]["unit"] == "n"} == {
        "C01": "count",
        "C02": "count",
        "C05": "count",
        "C06": "mean",
        "C24": "mean",
    }
    for ratio in ("C07", "C09", "C31"):
        assert cat[ratio]["kind"] is None, f"{ratio} is a rate, not a {cat[ratio]['kind']}"


def test_the_catalog_carries_prominence():
    """The render groups headline indicators from this; without it all 22 read equal."""
    from connect_labs.semantic.runtime import filter_to_series, load_registry, measure_catalog

    _, reg = load_registry()
    cat = {m["indicator"]: m for m in measure_catalog(filter_to_series(reg, "C"))}
    assert cat["C09"]["prominence"] == "Top"
    assert cat["C06"]["prominence"] == "Lower"
    assert all(m["prominence"] for m in cat.values()), "every indicator needs a prominence"


def test_filtering_to_a_series_keeps_the_availability_gates():
    """The gates are infrastructure, not part of any series.

    The reachability walk cannot find them: they carry no `meta`, so they are not
    roots, and no indicator's sql references them — they are read ALONGSIDE a value,
    not inside it. So `series=C` came back with no `anyrec_*` columns at all, and a
    caller had no way to tell "the app never asked this question" from "the answer
    is 0". A worker who logged no danger signs has not achieved a 0% danger-sign
    rate. That distinction was worth 268 of 5,302 per-FLW checks when it was ported.
    """
    from connect_labs.semantic.runtime import filter_to_series, load_registry, measure_catalog

    _, reg = load_registry()
    all_gates = {m["name"] for m in reg["measures"] if m.get("gate")}
    assert all_gates, "the registry must mark its gates explicitly, not by name prefix"

    for series, expected_indicators in (("C", 22), ("N", 14)):
        kept = filter_to_series(reg, series)
        names = {m["name"] for m in kept["measures"]}
        assert all_gates <= names, f"series={series} dropped gates: {sorted(all_gates - names)}"
        # and keeping them must not smuggle them into the display contract
        assert len(measure_catalog(kept)) == expected_indicators
