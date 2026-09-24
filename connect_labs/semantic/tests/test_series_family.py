"""An indicator's family is declared, not spelled into its id.

A series used to BE the letters an id starts with -- `C14` is in C -- so an id
could never be a plain name: `mortality` would have been a family of its own
called MORTALITY. A registry that declares one family now owns every indicator
in it, whatever the ids look like, and the prefix rule survives for the
registries (and frozen records) that still use codes.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.compiler import indicator_model_problems
from connect_labs.semantic.model import indicator_series, series_prefixes
from connect_labs.semantic.runtime import SemanticRuntimeError, filter_to_series, measure_catalog


def _indicator(name, **meta):
    return [
        {"name": name, "type": "number", "sql": "{" + name + "_numerator}", "meta": {"indicator": name, **meta}},
        {"name": f"{name}_numerator", "type": "count"},
        {"name": f"{name}_denominator", "type": "count"},
    ]


def _registry(series, *names):
    measures = [m for n in names for m in _indicator(n)]
    doc = {"measures": measures}
    if series is not None:
        doc["series"] = series
    return doc


def test_one_declared_family_owns_every_slug_id():
    reg = _registry(["kmc"], "mortality", "pct_slow_growth", "started_cases")
    assert series_prefixes(reg) == ("KMC",)
    assert {indicator_series(reg, m.get("meta")) for m in reg["measures"] if m.get("meta")} == {"KMC"}
    kept = {m["name"] for m in filter_to_series(reg, "kmc")["measures"]}
    assert {"mortality", "pct_slow_growth", "started_cases", "mortality_denominator"} <= kept


def test_the_catalog_states_each_indicators_family():
    """The benchmark publisher reads a frozen catalog, not the registry, so the
    family has to travel on each entry -- a slug's letters name nothing."""
    reg = _registry(["KMC"], "mortality")
    assert [e["series"] for e in measure_catalog(reg)] == ["KMC"]


def test_code_ids_still_resolve_by_prefix():
    reg = _registry(["Q"], "Q01", "Q02")
    assert filter_to_series(reg, "Q")["measures"]
    assert indicator_series(reg, {"indicator": "Q02"}) == "Q"


def test_an_undeclared_registry_reads_families_off_its_codes():
    """The live KMC record was saved before `series:` existed; it must keep
    resolving C and N from its ids."""
    reg = _registry(None, "C14", "N13")
    assert series_prefixes(reg) == ("C", "N")
    with pytest.raises(SemanticRuntimeError, match="unknown indicator series"):
        filter_to_series(reg, "KMC")


def test_an_explicit_meta_series_wins_over_the_prefix():
    reg = {"series": ["A", "B"], "measures": _indicator("A01", series="B")}
    assert indicator_series(reg, reg["measures"][0]["meta"]) == "B"
    assert not indicator_model_problems(reg)


def test_with_several_families_an_indicator_in_none_is_refused():
    """Otherwise it would silently vanish from every `?series=` request."""
    reg = _registry(["C", "N"], "C14", "mortality")
    problems = indicator_model_problems(reg)
    assert any("mortality" in p and "none of the declared series" in p for p in problems)
