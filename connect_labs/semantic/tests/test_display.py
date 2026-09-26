"""The registry display contract (semantic/display.py): defaults, and the save gate."""

import copy

import pytest

from connect_labs.semantic.display import display_problems, indicator_display, resolve_display
from connect_labs.semantic.runtime import load_deployment_facts, load_registry, measure_catalog
from connect_labs.semantic.validation import validate_registry


@pytest.fixture(scope="module")
def vq():
    return load_registry("visit_quality")


@pytest.fixture(scope="module")
def kmc():
    return load_registry("kmc")


def test_visit_quality_renders_out_of_the_box_on_defaults(vq):
    props, inds = vq
    d = resolve_display(props, inds)
    assert d["entity"] == {"name": "beneficiary", "plural": "beneficiaries", "key": "entity_id"}
    assert d["worker"] == {"name": "worker", "plural": "workers"}
    assert d["organisation"]["plural"] == "organisations"
    # No headline declared: the Top-prominence indicators, in registry order.
    assert d["headline"] == ["Q01", "Q02", "Q03"]
    assert d["categories"] == ["Reach", "Follow-up", "Quality"]
    # Scorecard: Top indicators when none declares `scorecard`.
    assert [i for i, x in d["indicators"].items() if x["scorecard"]] == ["Q01", "Q02", "Q03"]
    # Targets default to the green band edge for directed indicators.
    assert d["indicators"]["Q02"]["target"] == 60 and d["indicators"]["Q01"]["target"] is None
    assert [f["field"] for f in d["case_fields"]] == ["first_visit_date", "last_visit_date", "total_visits"]
    assert d["reading"] is None
    # The registry's own floor, so an "n<..." cell names it (was a measure's, 100).
    assert d["min_denominator"] == 5


def test_kmc_declares_the_five_tiles_its_report_hand_codes(kmc):
    props, inds = kmc
    d = resolve_display(props, inds)
    assert d["headline"] == [
        "started_cases",
        "pct_healthy_growth",
        "mean_early_growth_rate",
        "mortality",
        "lost_by_day_28",
    ]
    assert d["entity"]["plural"] == "babies" and d["reading"]["column"] == "weight_g"
    assert d["indicators"]["pct_healthy_growth"]["target"] == 70
    assert d["indicators"]["mortality"]["credibility"] == "mortality_recording_credible"


def test_both_shipped_registries_still_validate(vq, kmc):
    assert validate_registry(*vq, load_deployment_facts("visit_quality")) == []
    assert validate_registry(*kmc, load_deployment_facts("kmc")) == []


def test_the_catalog_carries_the_display_keys(vq):
    _props, inds = vq
    cat = {m["indicator"]: m for m in measure_catalog(inds)}
    assert cat["Q02"]["headline"] == 2 and cat["Q02"]["target"] == 60 and cat["Q02"]["scorecard"] is True
    assert cat["Q04"]["headline"] is None and cat["Q04"]["scorecard"] is False
    assert cat["Q01"]["label"] == "Beneficiaries reached"


def test_a_declared_headline_replaces_the_default():
    measures = [
        {"name": "a", "title": "A", "meta": {"indicator": "a", "prominence": "Top"}},
        {"name": "b", "title": "B", "meta": {"indicator": "b", "headline": 2}},
        {"name": "c", "title": "C", "meta": {"indicator": "c", "headline": True}},
        {"name": "d", "title": "D", "meta": {"indicator": "d", "headline": 1, "scorecard": False}},
    ]
    shown = indicator_display(measures)
    assert [i for i in ("a", "b", "c", "d") if shown[i]["headline"]] == ["b", "c", "d"]
    assert (shown["d"]["headline"], shown["b"]["headline"], shown["c"]["headline"]) == (1, 2, 3)
    # Once one indicator declares `scorecard`, undeclared ones fall back to prominence.
    assert shown["a"]["scorecard"] is True and shown["d"]["scorecard"] is False


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"display": {"entity": "baby"}}, "display.entity"),
        ({"display": {"colour": "red"}}, "unknown key"),
        ({"display": {"case_fields": [{"field": "a b"}]}}, "case_fields[0]"),
        ({"display": {"case_fields": [{"field": "a", "format": "money"}]}}, "format"),
        ({"display": {"headline_count": 0}}, "headline_count"),
        ({"display": {"reading": {"label": "x"}}}, "display.reading"),
    ],
)
def test_a_malformed_display_block_is_refused(vq, patch, message):
    props, inds = vq
    doc = {**copy.deepcopy(inds), **patch}
    assert any(message in e for e in validate_registry(props, doc, {}))


@pytest.mark.parametrize(
    "meta, message",
    [
        ({"headline": 0}, "meta.headline"),
        ({"headline": "yes"}, "meta.headline"),
        ({"target": "70"}, "meta.target"),
        ({"target": 700}, "percentage"),
        ({"order": "first"}, "meta.order"),
        ({"scorecard": 1}, "meta.scorecard"),
        ({"credibility": "nope"}, "deployment.settings does not declare"),
    ],
)
def test_malformed_indicator_display_meta_is_refused(vq, meta, message):
    props, inds = vq
    doc = copy.deepcopy(inds)
    q02 = next(m for m in doc["measures"] if (m.get("meta") or {}).get("indicator") == "Q02")
    q02["meta"].update(meta)
    assert any(message in e for e in validate_registry(props, doc, {})), validate_registry(props, doc, {})


def test_two_indicators_cannot_share_a_headline_position():
    doc = {
        "measures": [
            {"name": "a", "meta": {"indicator": "a", "headline": 1}},
            {"name": "b", "meta": {"indicator": "b", "headline": 1}},
        ]
    }
    assert any("position 1" in p for p in display_problems(doc))
