"""Making the engine generic must not move a single KMC number.

The engine used to BE the KMC model (entity key, cohort date, visit markers, weight
series, pipelines, a denominator floor -- all constants). They are now registry
data, and records saved before that have them supplied by `semantic/legacy.py`.
Three things have to hold, and each is pinned here by EXECUTING the compiled
rollup -- all nine scopes, suppression gates on -- over a fixture spread across
three opportunities, six workers, two LLOs and three cohort months:

  1. The shipped KMC registry, now declaring its model explicitly, returns exactly
     the rows in `fixtures/kmc_rollup_golden.json`, compared value for value
     (floats to 1e-12, for cross-version Postgres arithmetic). The file was first
     produced by the engine BEFORE it was made generic; it was regenerated once,
     deliberately, when #2004 merged the C and N indicator sets into one.
  2. The same registry in the pre-model LEGACY shape (bare `entity: baby`, no
     visit_columns / pipelines / value_column / defaults / series) -- the shape of
     a record saved before the model existed, as live record 19784 was -- returns
     the same rows, through the shim. Dropping `series` and `defaults` changes
     how ids group into families and where a cell is graded "insufficient", not
     one computed value: both act after the rows (test_series_family.py covers an
     undeclared registry's families).
  3. The two compile to the same SQL.

Runs whenever Postgres is reachable (SEMANTIC_TEST_DSN, defaulting to the CI
service's postgres/postgres@127.0.0.1). The fixture is a TEMP table, so xdist
workers sharing that database cannot race on it.

If a KMC DEFINITION changes deliberately, regenerate the golden file from the
new registry (`python -m connect_labs.semantic.tests.test_engine_parity`) and say
so in the PR -- the point of the file is that nothing else ever moves it.
"""

from __future__ import annotations

import copy
import json

import pytest

from connect_labs.semantic.compiler import compile_rollup_sql
from connect_labs.semantic.model import resolve_model
from connect_labs.semantic.runtime import load_deployment_facts, load_registry
from connect_labs.semantic.tests import parity_fixture as pf
from connect_labs.semantic.tests.pg import DSN, connect_or_skip

psycopg2 = pytest.importorskip("psycopg2")


def _kmc():
    props, inds = load_registry("kmc")
    return props, inds, load_deployment_facts("kmc")


def legacy_shape(props: dict, inds: dict) -> tuple[dict, dict]:
    """The KMC documents as a record saved before the model existed stores them."""
    props, inds = copy.deepcopy(props), copy.deepcopy(inds)
    props["entity"] = "baby"
    for section in ("visit_columns", "pipelines"):
        props.pop(section, None)
    props["weight_series"].pop("value_column", None)
    inds.pop("defaults", None)
    inds.pop("series", None)
    return props, inds


@pytest.fixture(scope="module")
def conn():
    c = connect_or_skip("the engine parity test")
    yield c
    c.close()


@pytest.fixture(scope="module")
def visit_sql(conn):
    return pf.load(conn)


def test_the_shipped_kmc_registry_reproduces_the_pre_refactor_rows(conn, visit_sql):
    props, inds, facts = _kmc()
    rows = pf.run_rollup(conn, props, inds, facts, visit_sql)
    golden = json.loads(pf.GOLDEN.read_text())
    assert {r["scope"] for r in rows} == set(pf.ALL_SCOPES)
    diff = pf.rows_differ(golden, rows, float_rel_tol=1e-12)
    assert not diff, f"{len(diff)} difference(s) from the pre-refactor engine:\n  " + "\n  ".join(diff[:40])


def test_a_legacy_shaped_record_reproduces_them_too(conn, visit_sql):
    props, inds, facts = _kmc()
    old_props, old_inds = legacy_shape(props, inds)
    assert resolve_model(old_props, old_inds).shimmed, "the legacy shape must actually exercise the shim"
    rows = pf.run_rollup(conn, old_props, old_inds, facts, visit_sql)
    golden = json.loads(pf.GOLDEN.read_text())
    diff = pf.rows_differ(golden, rows, float_rel_tol=1e-12)
    assert not diff, f"{len(diff)} difference(s) through the legacy shim:\n  " + "\n  ".join(diff[:40])


def test_explicit_and_legacy_kmc_compile_to_the_same_sql():
    props, inds, facts = _kmc()
    old_props, old_inds = legacy_shape(props, inds)
    kw = {"scopes": pf.ALL_SCOPES, "as_of": pf.AS_OF, "llo_map": pf.LLO_MAP, "settings": facts["settings"]}
    assert compile_rollup_sql(props, inds, "SELECT 1", **kw) == compile_rollup_sql(
        old_props, old_inds, "SELECT 1", **kw
    )


def test_the_shipped_kmc_registry_does_not_lean_on_the_shim():
    """The legacy module is for records nobody can edit. The file we CAN edit
    declares every section, so nothing about KMC is hidden in engine code."""
    props, inds = load_registry("kmc")
    model = resolve_model(props, inds)
    assert model.shimmed == (), f"registry/kmc still relies on legacy.py for {model.shimmed}"
    # 20, the compute spec's floor, since #2004 (the workbook's was 25)
    assert model.min_denominator == 20 and model.value_column == "weight_g"


def test_the_shim_applies_only_to_a_document_that_predates_the_model():
    """A new-shape registry that leaves a section out gets NOTHING of KMC's."""
    props, inds = load_registry("kmc")
    bare = copy.deepcopy(props)
    for section in ("visit_columns", "pipelines"):
        bare.pop(section)
    inds = {k: v for k, v in inds.items() if k != "defaults"}
    model = resolve_model(bare, inds)
    assert model.shimmed == ()
    assert model.visit_columns == () and model.entity_pipeline is None and model.min_denominator is None


if __name__ == "__main__":  # pragma: no cover - regenerating the golden file is deliberate
    c = psycopg2.connect(DSN)
    props, inds, facts = _kmc()
    rows = pf.run_rollup(c, props, inds, facts, pf.load(c))
    pf.GOLDEN.write_text("[\n" + ",\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n]\n")
    print(f"wrote {len(rows)} rows to {pf.GOLDEN}")
