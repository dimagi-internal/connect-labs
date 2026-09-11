"""The registry compiles, validates, and produces the numbers the JS produces.

The last part is the point. A compiler that emits plausible SQL proves nothing;
these tests execute it against a real Postgres fixture and assert the indicator
values equal what kmc_programme_metrics_render.js computes in the browser for the
same cases. Parity is the whole claim of the new approach.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from connect_labs.semantic.compiler import (
    CUBE_MEASURE_TYPES,
    RegistryError,
    compile_indicator_sql,
    compile_measures,
    validate,
)

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"


@pytest.fixture(scope="module")
def props_doc():
    return yaml.safe_load((REGISTRY / "properties.yml").read_text())


@pytest.fixture(scope="module")
def registry():
    return yaml.safe_load((REGISTRY / "indicators.yml").read_text())


# ── The registry is well-formed ──────────────────────────────────────────────


def test_every_measure_type_is_real_cube(registry):
    """No invented dialect. Scout stayed inside this vocabulary; so do we."""
    for m in registry["measures"]:
        assert m["type"] in CUBE_MEASURE_TYPES, f"{m['name']} uses {m['type']}"


def test_all_22_live_indicators_present(registry):
    """The C-series, pinned. Scoped to C-prefixed ids because the registry now also
    carries the N-series (Neal's demo spec) as a SEPARATE series — the two are pinned
    by their own tests so neither can silently absorb the other."""
    ids = {
        m["meta"]["indicator"]
        for m in registry["measures"]
        if m.get("meta") and str(m["meta"]["indicator"]).startswith("C")
    }
    expected = {
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
    assert ids == expected


def test_every_indicator_carries_its_denominator(registry):
    """The workbook's no-bare-numbers rule, enforced structurally."""
    names = {m["name"] for m in registry["measures"]}
    for m in registry["measures"]:
        if not m.get("meta"):
            continue
        assert f"{m['name']}_denominator" in names, f"{m['name']} has no denominator measure"


def test_registry_validates_against_properties(props_doc, registry):
    """Every {CUBE}.col resolves. This is the check a Cube runtime would do."""
    assert validate(props_doc, registry) == []


def test_validate_catches_an_unknown_column(props_doc, registry):
    broken = {"measures": [{"name": "x", "type": "count", "filters": [{"sql": "{CUBE}.no_such_column"}]}]}
    problems = validate(props_doc, broken)
    assert any("no_such_column" in p for p in problems)


def test_rejects_a_type_outside_cube(props_doc):
    """`first`/`last` are exactly what we must NOT invent -- they belong below Layer 3."""
    broken = {"measures": [{"name": "x", "type": "first", "sql": "{CUBE}.reg_date"}]}
    with pytest.raises(RegistryError, match="outside Cube's measure vocabulary"):
        compile_measures(broken)


def test_number_measures_inline_their_siblings(registry):
    compiled = compile_measures(registry)
    c09 = compiled["c09"]
    assert "COUNT(*) FILTER" in c09  # numerator inlined
    assert "NULLIF" in c09  # denominator guarded
    assert "{" not in c09  # every reference resolved


def test_compiles_for_every_intrinsic_scope(props_doc, registry):
    for scope in ("programme", "opportunity", "flw", "month"):
        sql = compile_indicator_sql(props_doc, registry, "SELECT 1", scope=scope)
        assert "WITH visits_all AS" in sql
        assert "{CUBE}" not in sql  # no unresolved placeholders
        assert ":ELIG_DAYS" not in sql  # constants substituted


def test_llo_scope_without_a_map_is_refused(props_doc, registry):
    """`llo` is not on a visit row. Emitting SQL for it was a runtime failure.

    The compiler used to happily produce `props.llo` and fail at execution with
    "column props.llo does not exist" -- and validate() missed it because `llo`
    had been whitelisted in the known-columns set, i.e. the check defeated itself.
    """
    with pytest.raises(RegistryError, match="llo_map"):
        compile_indicator_sql(props_doc, registry, "SELECT 1", scope="llo")


def test_llo_scope_with_a_map_compiles(props_doc, registry):
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1", scope="llo", llo_map={10042: "PIPN"})
    assert "AS llo" in sql
    assert "props.llo" in sql


def test_suppression_needs_the_scope_it_is_declared_on(props_doc, registry):
    """Silently not suppressing is the failure the gate exists to prevent."""
    with pytest.raises(RegistryError, match="no llo_map"):
        compile_indicator_sql(
            props_doc,
            registry,
            "SELECT 1",
            scope="programme",
            settings={"mortality_recording_credible": {"PIPN": True}},
        )


def test_suppression_emits_a_column_per_rule(props_doc, registry):
    sql = compile_indicator_sql(
        props_doc,
        registry,
        "SELECT 1",
        scope="llo",
        llo_map={10042: "PIPN", 1487: "GHI"},
        settings={"mortality_recording_credible": {"PIPN": True, "GHI": False}},
    )
    assert "c14_suppressed" in sql
    assert "'PIPN'" in sql  # the credible list drives the NOT IN


def test_no_suppression_columns_when_no_settings(props_doc, registry):
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1", scope="programme")
    assert "_suppressed" not in sql


def test_unknown_scope_is_loud(props_doc, registry):
    with pytest.raises(RegistryError, match="unknown scope"):
        compile_indicator_sql(props_doc, registry, "SELECT 1", scope="galaxy")


def test_the_N_series_is_nreal_lesh_demo_spec_exactly(registry):
    """Neal's demo compute spec (2026-09-05), pinned as its own set: all fifteen.

    N05 (median gestational age) was held back until Layer 1 carried its column
    (`gestational_age_wks`, from gestational_age_at_birth_lmp); the pipeline has it
    now, so the scorecard is complete.
    """
    ids = {
        m["meta"]["indicator"]
        for m in registry["measures"]
        if m.get("meta") and str(m["meta"]["indicator"]).startswith("N")
    }
    assert ids == {f"N{i:02d}" for i in range(1, 16)}


def test_the_growth_quality_shares_share_one_denominator(registry):
    """N09-N12 are shares OF QUALIFYING SVNs and sum to 100% over that set. If any one
    of them drifted onto its own denominator they would stop summing and nobody reading
    the dashboard would be able to tell."""
    by_name = {m["name"]: m for m in registry["measures"]}
    dens = {by_name[f"n{i:02d}_denominator"]["filters"][0]["sql"] for i in (9, 10, 11, 12)}
    assert len(dens) == 1, f"growth-quality shares disagree on their denominator: {dens}"
    # qualifying_spec since the N-series moved onto the demo compute spec's own
    # definitions (started = 2 visits, 21-day window); the invariant is the SHARED
    # denominator, not its name.
    assert "qualifying" in dens.pop()


def test_a_qualifying_svn_must_have_a_computable_velocity():
    """Neal's compute spec v3, section 3: qualifying = eligible_42d AND banded AND
    weight_gain_data_computable.

    v1 of the spec -- and this registry until 2026-09-10 -- left out the third
    term. That kept every baby with no usable weight series in the N09-N12
    denominator and counted it as "incomplete", so it read as poor growth: PIPN's
    healthy-growth share came out at 59 percent against v3's 72, and GHI's at 26
    against 39. Nothing caught it, because nothing pinned the N-series to the spec;
    this does.

    Pinned against properties.yml, which seeds the live registry record -- so a
    reseed from disk cannot quietly put the error back.
    """
    import yaml as _yaml

    props = {p["name"]: p for p in _yaml.safe_load((REGISTRY / "properties.yml").read_text())["properties"]}
    sql = props["qualifying_spec"]["sql"]
    for term in ("eligible_42d_spec", "birthweight_band IS NOT NULL", "computable_spec"):
        assert term in sql, f"qualifying_spec is missing {term!r}: {sql}"


def test_incomplete_growth_data_is_only_the_unreliable_qualifying_cases(registry):
    """v3 item 12: incomplete = computable AND NOT sufficient, over qualifying.

    The numerator is written as `qualifying AND growth_class IS NULL`. That equals
    v3's definition ONLY because qualifying now requires a computable velocity and
    a birthweight band: growth_class is null exactly when a case is not sufficient
    or has no band, and qualifying has already excluded no-band. Without the
    computable term the same expression swept the discarded no-data babies back
    into "incomplete". So the numerator and the denominator's definition are pinned
    together -- change either alone and N09-N12 stop partitioning the right set.
    """
    import yaml as _yaml

    by_name = {m["name"]: m for m in registry["measures"]}
    num = by_name["n12_numerator"]["filters"][0]["sql"]
    assert "qualifying_spec" in num and "growth_class_spec IS NULL" in num

    props = {p["name"]: p for p in _yaml.safe_load((REGISTRY / "properties.yml").read_text())["properties"]}
    growth = props["growth_class_spec"]["sql"]
    assert "NOT sufficient_spec THEN NULL" in growth
    assert "birthweight_band IS NULL THEN NULL" in growth
    assert "computable_spec" in props["qualifying_spec"]["sql"]


def test_the_banded_growth_table_is_neals_not_a_flat_guess(registry):
    """The C-series still carries a flat PLAUSIBLE_LO/HI 10-20 marked PROVISIONAL. The
    N-series must use the per-birthweight-band table, because a flat band is wrong at
    both ends: it calls a healthy 2,500g baby fast (real ceiling 18) and a struggling
    sub-1,000g baby plausible (real floor 9)."""
    import yaml as _yaml

    props = _yaml.safe_load((REGISTRY / "properties.yml").read_text())["properties"]
    by_name = {p["name"]: p for p in props}
    lo, hi = by_name["growth_plausible_lo"]["sql"], by_name["growth_plausible_hi"]["sql"]
    for band, l, h in [
        ("<1000", "9", "30"),
        ("1000-1499", "6", "28"),
        ("1500-1999", "6", "24"),
        ("2000-2499", "5", "20"),
        ("2500+", "0", "18"),
    ]:
        assert f"'{band}' THEN {l}" in lo, f"{band} floor"
        assert f"'{band}' THEN {h}" in hi, f"{band} ceiling"
    assert "PLAUSIBLE_LO" not in by_name["growth_class_banded"]["sql"]


def test_no_compiled_sql_contains_a_bare_percent_operator():
    """psycopg2 treats % as a parameter placeholder, so a literal modulo in the SQL
    makes `cursor.execute(sql)` raise `tuple index out of range` before the query
    reaches Postgres at all.

    This escaped every existing test because the parity suite uses RAW psycopg2,
    which only interpolates when params are passed. The endpoint goes through
    Django's cursor, which is the path that trips — so the failure appeared only on
    the first live call, as an opaque 500.

    Use MOD(x, y). It is standard SQL and carries no placeholder ambiguity.
    """
    import yaml as _yaml

    from connect_labs.semantic.compiler import compile_rollup_sql

    props = _yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = _yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    sql = compile_rollup_sql(props, registry, "SELECT * FROM v", scopes=["programme"])

    offenders = [ln.strip() for ln in sql.split("\n") if "%" in ln]
    assert not offenders, (
        "compiled SQL contains a bare % (psycopg2 reads it as a placeholder); " f"use MOD() instead: {offenders[:3]}"
    )


def test_month_cohorting_falls_back_to_the_first_visit(props_doc, registry):
    """A baby with no reg_date must still land in a month.

    `reg_date` is a FILTERed MIN over the visits, so a baby whose rows never
    carried one aggregates to NULL. Truncating reg_date alone puts that baby in
    no month at all -- it silently vanishes from the trend rather than being
    cohorted -- and the render has always fallen back to the first visit for
    exactly this reason (`m(r.reg_date) || m(r.first_visit)`).

    Parity is why this went unnoticed: it covered programme, opportunity, llo and
    flw, and NOT month -- the one scope this column exists for. So the check is a
    static one on the compiled SQL, which is what the omission left uncovered.
    """
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1", scope="month")
    assert "AS cohort_month" in sql, "no cohort_month column was compiled for the month scope"
    window = sql[sql.index("DATE_TRUNC(") : sql.index("AS cohort_month")]
    assert "COALESCE" in window, "cohort_month must fall back, not truncate reg_date alone"
    assert "first_visit" in window, "the fallback must be the first visit, as the render does"


def test_the_visit_set_itself_is_cut_at_as_of(props_doc, registry):
    """ "As of 6 Sep" must mean the data as it stood on 6 Sep.

    Every maturity gate measured against `:as_of`, but the visit set the gates ran
    over was never filtered -- so a run for a past week counted visits that had
    not happened yet, and only the eligibility side of the figures moved with the
    date. The cut is the whole as-of day inclusive.
    """
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1", as_of="DATE '2026-09-06'")
    assert "visits_all AS" in sql, "the raw visit set must be kept apart from the as-of one"
    window = sql[sql.index("visits AS (", sql.index("visits_all AS")) : sql.index("weight_days AS")]
    assert "visit_date <" in window, "the visit set is not cut at the report date"
    assert "DATE '2026-09-06'" in window, "the cut must use the same as_of every gate uses"
    assert "+ 1" in window, "the as-of day itself must be included"


def test_the_default_as_of_is_today(props_doc, registry):
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1")
    window = sql[sql.index("visits AS (", sql.index("visits_all AS")) : sql.index("weight_days AS")]
    assert "CURRENT_DATE" in window


def test_a_seed_reading_is_declared_as_data_and_never_pairs_with_a_visit(props_doc, registry):
    """The demo compute spec's weight series is the enrolment weight at reg_date PLUS
    every visit weight, with a same-day visit winning and an enrolment weight within
    1 g of birth weight dropped as a re-entry. That is `weight_series.seed_reading`,
    registry data. The seed reading must count as a measured day (the spec's "thin"
    rule) but never form a pair with a visit reading (the spec excludes the
    enrolment->visit-1 rebound), and the C-series must not see it at all."""
    sql = compile_indicator_sql(props_doc, registry, "SELECT 1")
    readings = sql[sql.index("weight_readings AS") : sql.index("weight_days AS")]
    assert "UNION ALL" in readings and "TRUE AS is_seed" in readings
    assert "MIN(reg_date)::date AS day" in readings and "MIN(enrollment_weight_g) AS weight_g" in readings
    assert "MIN(birth_weight_g) AS birth_weight_g" in readings, "a column the exclude rule names is MIN()'d too"
    assert "ABS(weight_g - birth_weight_g) < 1" in readings
    seq = sql[sql.index("weight_seq AS") : sql.index("weight_agg AS")]
    assert "PARTITION BY wd.baby_id, wd.is_seed ORDER BY wd.day) AS prev_w" in seq
    assert "PARTITION BY wd.baby_id, wd.is_seed ORDER BY wd.day) AS prev_day" in seq
    # the window anchors on the first MEASURED weighing -- the spec's "first 21
    # days of the VISIT weight series" -- not on the seed; the C-series
    # expressions exclude the seed too
    assert "MIN(wd.day) FILTER (WHERE NOT wd.is_seed) OVER (PARTITION BY wd.baby_id))::int AS series_day" in seq
    agg = sql[sql.index("weight_agg AS") : sql.index("visit_agg AS")]
    assert "COUNT(*) FILTER (WHERE NOT is_seed) AS n_weight_days" in agg
    assert "COUNT(*) AS n_measured_days" in agg


def test_a_registry_without_a_seed_reading_compiles_as_before(props_doc, registry):
    import copy

    plain = copy.deepcopy(props_doc)
    plain["weight_series"].pop("seed_reading")
    sql = compile_indicator_sql(plain, registry, "SELECT 1")
    readings = sql[sql.index("weight_readings AS") : sql.index("weight_days AS")]
    assert "UNION ALL" not in readings
    assert "FALSE AS is_seed" in readings, "every reading is measured; is_seed still exists for the derived SQL"


def test_explain_returns_the_whole_chain_behind_an_indicator(props_doc, registry):
    """A second engine has to be able to read the exact logic behind a number. For
    N15 that is the measure expression, its numerator/denominator, flag_impossible ->
    any_impossible_step (a weight-series derivation), the constants it substitutes,
    and a compiled statement -- in evaluation order, with no database."""
    from connect_labs.semantic.explain import UnknownIndicator, explain

    out = explain(props_doc, registry, "N15", llo_map={10042: "BERI"})
    assert out["indicator"] == "N15" and out["measure"] == "n15"
    assert {c["name"] for c in out["components"]} == {"n15_numerator", "n15_denominator"}
    names = [p["name"] for p in out["properties"]]
    assert "flag_impossible" in names and "computable_spec" in names and "velocity_spec" in names
    # evaluation order: velocity_spec before computable_spec before sufficient chain
    assert names.index("velocity_spec") < names.index("computable_spec")
    derived = {d["name"] for d in out["weight_series"]["derived"]}
    assert "any_impossible_step" in derived and "win_mean_w" in derived
    assert out["constants"]["IMPOSSIBLE_LO"] == -20 and out["constants"]["IMPOSSIBLE_HI"] == 45
    assert out["weight_series"]["seed_reading"]["value"] == "enrollment_weight_g"
    # constants are substituted in the SQL a reader sees
    assert ":IMPOSSIBLE_LO" not in "".join(d["sql"] for d in out["weight_series"]["derived"])
    assert "NOT BETWEEN -20 AND 45" in "".join(d["sql"] for d in out["weight_series"]["derived"])
    assert "FILTER (WHERE (props.flag_impossible AND props.computable_spec))" in out["expression"]["compiled"]
    assert "pipeline_visit_rows" in out["compiled_sql"] and "any_impossible_step" in out["compiled_sql"]
    # ids resolve case-insensitively and by measure name
    assert explain(props_doc, registry, "n15")["measure"] == "n15"
    assert explain(props_doc, registry, "c14")["indicator"] == "C14"
    with pytest.raises(UnknownIndicator):
        explain(props_doc, registry, "N99")


def test_the_case_scope_groups_by_baby(props_doc, registry):
    """One row per baby: how a measure becomes a contribution. The worker review's
    case table is this scope for one worker -- the same registry read one grouping
    level further down, not a second implementation in the browser."""
    from connect_labs.semantic.compiler import compile_rollup_sql

    sql = compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=["case"])
    sets = sql[sql.index("GROUPING SETS") :]
    assert "props.case_id" in sets
    assert "props.username" in sets and "props.opportunity_id" in sets


def test_a_visit_filter_is_pushed_below_layer_2(props_doc, registry):
    """Filtering the grouped output would still pay for the whole cohort's
    extraction; the filter has to cut the visit set. Values are escaped, keys are
    whitelisted, and an unknown key is refused rather than matching nothing."""
    from connect_labs.semantic.compiler import RegistryError, compile_rollup_sql

    sql = compile_rollup_sql(
        props_doc,
        registry,
        "SELECT 1",
        scopes=["case"],
        visit_filter={"opportunity_id": "10042", "username": "flw'001"},
    )
    visits_cte = sql[sql.index("visits AS (", sql.index("visits_all AS")) : sql.index("weight_days AS")]
    assert "AND opportunity_id = 10042" in visits_cte
    assert "AND username = 'flw''001'" in visits_cte, "the quote must be escaped, not interpolated"
    with pytest.raises(RegistryError):
        compile_rollup_sql(props_doc, registry, "SELECT 1", scopes=["case"], visit_filter={"llo": "PIPN"})


def test_every_indicator_has_english_rendered_from_its_sql(props_doc, registry):
    """A programme manager reads the definition; an agent reads the SQL; both must
    come from the same registry so they cannot disagree. The mechanical sentence
    is rendered from the measure, so it exists for every indicator; the authored
    `plain` (the demo compute spec's wording) exists for the whole N-series."""
    from connect_labs.semantic.explain import english, explain, to_markdown, to_sql

    tops = [m for m in registry["measures"] if (m.get("meta") or {}).get("indicator")]
    for m in tops:
        en = english(registry, props_doc, m["name"])
        assert en["definition"] and en["definition"][0].isupper(), m["name"]
        if m["meta"]["indicator"].startswith("N"):
            assert en["plain"], f"{m['meta']['indicator']} has no authored plain-English definition"
    n15 = english(registry, props_doc, "n15")
    assert "as a percentage of" in n15["definition"] and "computable spec" in n15["definition"]
    assert any(r["name"] == "flag_impossible" for r in n15["reads"])
    n07 = english(registry, props_doc, "n07")
    assert n07["definition"].startswith("The sum of visits spec over babies where eligible 42d spec, divided by")

    exps = [explain(props_doc, registry, i) for i in ("N15", "C14")]
    md = to_markdown(exps, registry_label="test")
    assert "## N15" in md and "## C14" in md and "```sql" in md and "flag_impossible" in md
    sql = to_sql(exps, registry_label="test")
    assert sql.startswith("-- Indicator definitions") and "pipeline_visit_rows" in sql and "-- N15" in sql
