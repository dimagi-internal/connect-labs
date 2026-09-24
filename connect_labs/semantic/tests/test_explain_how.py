"""The definition a programme manager reads: how an indicator is counted, and what
each term means -- in plain words, from the registry, never the developers' notes."""

import re

import pytest

from connect_labs.semantic.explain import english
from connect_labs.semantic.runtime import resolve_registry

PARTNERS = ("PIPN", "NAMA", "GHI", "EHA", "BERI", "Kikapu")


@pytest.fixture(scope="module")
def registry():
    props, reg, *_ = resolve_registry({}, None)
    return props, reg


def _indicators(reg):
    return [(m.get("meta") or {}).get("indicator") for m in reg["measures"] if (m.get("meta") or {}).get("indicator")]


def test_a_growth_share_reads_as_out_of_and_counts(registry):
    props, reg = registry
    how = english(reg, props, "pct_fast_growth")["how"]
    assert how == {
        "kind": "percent",
        "base": {"what": "babies", "where": ["Qualifies for growth review"]},
        "counts": {"what": "babies", "where": ["Growth class: fast"]},
        "shown_when": "at least 20 in the base",
    }


def test_a_value_indicator_names_what_it_takes_the_median_of(registry):
    props, reg = registry
    how = english(reg, props, "median_birthweight")["how"]
    assert how["kind"] == "value"
    assert how["value"] == "median birthweight (g)"


def test_every_term_an_indicator_reads_is_defined_in_plain_words(registry):
    """Every property or aggregate behind an indicator carries a label and a
    `means`, so the popup never falls back to a raw column name or to notes."""
    props, reg = registry
    for ind in _indicators(reg):
        for r in english(reg, props, ind)["reads"]:
            assert r["means"], f"{ind}: {r['name']} has no plain definition"
            assert r["label"] != r["name"], f"{ind}: {r['name']} has no label"


def test_no_plain_definition_names_a_partner_or_carries_history(registry):
    """`notes` hold the developers' rationale -- 'v1 omitted ...: PIPN read 59
    percent instead of 72' -- which read as though the definition were specific
    to one partner. A definition shown to a reader must not."""
    props, _ = registry
    for item in list(props["properties"]) + list(props["aggregates"]):
        text = f"{item.get('label', '')} {item.get('means', '')}"
        for partner in PARTNERS:
            assert not re.search(rf"\b{partner}\b", text), f"{item['name']} names {partner}"
        assert not re.search(r"\bv\d\b|\bused to\b|\binstead of\b", text), f"{item['name']} carries history"


def test_reads_never_surface_the_developer_notes(registry):
    props, reg = registry
    reads = {r["name"]: r for r in english(reg, props, "pct_fast_growth")["reads"]}
    notes = next(p["notes"] for p in props["properties"] if p["name"] == "growth_qualifying")
    assert "PIPN" in notes, "fixture assumption: the notes do carry the history"
    assert reads["growth_qualifying"]["means"] != notes
    assert "PIPN" not in reads["growth_qualifying"]["means"]
