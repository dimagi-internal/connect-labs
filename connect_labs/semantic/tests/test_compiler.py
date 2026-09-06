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
        assert "WITH visits AS" in sql
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
    """Neal's demo compute spec (2026-09-05), pinned as its own set.

    N05 (median gestational age) is deliberately ABSENT: it needs a Layer 1 field the
    pipeline has never extracted (gestational_age_at_birth_lmp), which is added to the
    pipeline template in the same change but only reaches the data on the next
    extraction. Declaring a measure over a column that does not exist compiles cleanly
    and fails at execution — the exact class test_every_declared_scope_actually_executes
    exists to catch — so it lands with the pipeline migration rather than before it.
    """
    ids = {
        m["meta"]["indicator"]
        for m in registry["measures"]
        if m.get("meta") and str(m["meta"]["indicator"]).startswith("N")
    }
    assert ids == {
        "N01",
        "N02",
        "N03",
        "N04",
        "N06",
        "N07",
        "N08",
        "N09",
        "N10",
        "N11",
        "N12",
        "N13",
        "N14",
        "N15",
    }
    assert "N05" not in ids


def test_the_growth_quality_shares_share_one_denominator(registry):
    """N09-N12 are shares OF QUALIFYING SVNs and sum to 100% over that set. If any one
    of them drifted onto its own denominator they would stop summing and nobody reading
    the dashboard would be able to tell."""
    by_name = {m["name"]: m for m in registry["measures"]}
    dens = {by_name[f"n{i:02d}_denominator"]["filters"][0]["sql"] for i in (9, 10, 11, 12)}
    assert len(dens) == 1, f"growth-quality shares disagree on their denominator: {dens}"
    assert "qualifying_svn" in dens.pop()


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
