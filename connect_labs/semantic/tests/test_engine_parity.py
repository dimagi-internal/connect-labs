"""Making the engine generic must not move a single KMC number.

The engine used to BE the KMC model (entity key, cohort date, visit markers, weight
series, pipelines, a denominator floor -- all constants). They are now registry
data, and records saved before that have them supplied by `semantic/legacy.py`.
Three things have to hold, and each is pinned here by EXECUTING the compiled
rollup -- all nine scopes, suppression gates on -- over a fixture spread across
three opportunities, six workers, two LLOs and three cohort months:

  1. The KMC registry, declaring its model explicitly, returns exactly the rows in
     `fixtures/kmc_rollup_golden.json`, compared value for value (floats to 1e-12,
     for cross-version Postgres arithmetic). The file was first produced by the
     engine BEFORE it was made generic; it was regenerated once, deliberately,
     when #2004 merged the C and N indicator sets into one.
  2. The same registry in the pre-model LEGACY shape (bare `entity: baby`, no
     visit_columns / pipelines / value_column / defaults / series) -- the shape of
     a record saved before the model existed, as live record 19784 was -- returns
     the same rows, through the shim. Dropping `series` and `defaults` changes
     how ids group into families and where a cell is graded "insufficient", not
     one computed value: both act after the rows (test_series_family.py covers an
     undeclared registry's families).
  3. The two compile to the same SQL.

WHICH REGISTRY. These checks run against a FROZEN copy of the KMC registry as it
stood when the golden rows were pinned -- the #2004 / v3 compute-spec definitions,
`fixtures/kmc_v3_registry/{properties,indicators,deployment}.yml`, copied verbatim
from origin/main before Neal Lesh's metrics workbook (2026-10-03) changed the
shipped definitions. This test guards the ENGINE, not the definitions: a
deliberate definitions change legitimately moves the shipped registry's numbers,
and regenerating the golden from it would leave nothing that remembers what the
pre-refactor engine returned. Freezing the input keeps that memory -- any diff now
is the engine (compiler, model, legacy shim) changing how a fixed registry
computes. Whether the SHIPPED definitions compute what they say is test_parity.py's
job, against an independent Python reference. Never edit the frozen copy to track
the live registry; if the engine itself must change a number on purpose,
regenerate the golden from the frozen copy and say so in the PR.

Runs whenever Postgres is reachable (SEMANTIC_TEST_DSN, defaulting to the CI
service's postgres/postgres@127.0.0.1). The fixture is a TEMP table, so xdist
workers sharing that database cannot race on it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from connect_labs.semantic.compiler import compile_rollup_sql
from connect_labs.semantic.model import resolve_model
from connect_labs.semantic.runtime import load_deployment_facts, load_registry, normalise_deployment_facts
from connect_labs.semantic.tests import parity_fixture as pf
from connect_labs.semantic.tests.pg import DSN, connect_or_skip

psycopg2 = pytest.importorskip("psycopg2")


# The KMC registry the golden rows were pinned from (see the module docstring).
FROZEN_V3 = Path(__file__).resolve().parent / "fixtures" / "kmc_v3_registry"


def _kmc():
    """The frozen v3 KMC registry and its deployment facts -- not registry/kmc."""

    def doc(name):
        return yaml.safe_load((FROZEN_V3 / f"{name}.yml").read_text())

    return doc("properties"), doc("indicators"), normalise_deployment_facts(doc("deployment"))


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


def test_the_frozen_kmc_registry_reproduces_the_pre_refactor_rows(conn, visit_sql):
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


def _shipped():
    props, inds = load_registry("kmc")
    return props, inds, load_deployment_facts("kmc")


@pytest.mark.parametrize("registry", [_kmc, _shipped], ids=["frozen-v3", "shipped"])
def test_explicit_and_legacy_kmc_compile_to_the_same_sql(registry):
    """Needs no database, so it also holds the SHIPPED registry to it: whatever the
    definitions say, the explicit model and the shim must compile them identically."""
    props, inds, facts = registry()
    old_props, old_inds = legacy_shape(props, inds)
    kw = {"scopes": pf.ALL_SCOPES, "as_of": pf.AS_OF, "llo_map": pf.LLO_MAP, "settings": facts["settings"]}
    assert compile_rollup_sql(props, inds, "SELECT 1", **kw) == compile_rollup_sql(
        old_props, old_inds, "SELECT 1", **kw
    )


def test_the_shipped_kmc_registry_does_not_lean_on_the_shim():
    """The legacy module is for records nobody can edit. The file we CAN edit
    declares every section, so nothing about KMC is hidden in engine code.

    This one reads the SHIPPED registry, not the frozen copy: it is about the file
    that seeds live records. The frozen copy must not lean on the shim either, or
    the golden comparison above would be testing legacy.py's defaults rather than
    the explicit model."""
    props, inds = load_registry("kmc")
    model = resolve_model(props, inds)
    assert model.shimmed == (), f"registry/kmc still relies on legacy.py for {model.shimmed}"
    # 25, the floor in Neal's metrics workbook (2026-10-03); #2004 had used the v3 spec's 20
    assert model.min_denominator == 25 and model.value_column == "weight_g"

    frozen_props, frozen_inds, _ = _kmc()
    frozen = resolve_model(frozen_props, frozen_inds)
    assert frozen.shimmed == (), f"the frozen v3 registry relies on legacy.py for {frozen.shimmed}"
    assert frozen.min_denominator == 20, "the frozen copy has drifted from v3"


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
    # From the FROZEN registry: only an intended engine change may move the golden.
    c = psycopg2.connect(DSN)
    props, inds, facts = _kmc()
    rows = pf.run_rollup(c, props, inds, facts, pf.load(c))
    pf.GOLDEN.write_text("[\n" + ",\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n]\n")
    print(f"wrote {len(rows)} rows to {pf.GOLDEN}")
