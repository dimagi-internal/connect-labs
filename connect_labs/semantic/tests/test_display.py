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
        ({"display": {"visit_fields": [{"field": "a b"}]}}, "visit_fields[0]"),
        ({"display": {"visit_flags": "repeat_counts_flag"}}, "display.visit_flags"),
        ({"display": {"visit_flags": [{"label": "Repeat"}]}}, "visit_flags[0]"),
        ({"display": {"visit_flags": [{"column": "f", "value": ["yes"]}]}}, "visit_flags[0].value"),
        ({"display": {"visit_flags": [{"column": "f", "fields": "a"}]}}, "visit_flags[0].fields"),
        ({"display": {"visit_flags": [{"column": "f", "fields": ["a b"]}]}}, "visit_flags[0].fields"),
        ({"display": {"visit_flags": [{"column": "f", "description": 3}]}}, "visit_flags[0].description"),
        ({"display": {"entity": {"name": "child", "label_field": "child name"}}}, "display.entity.label_field"),
        ({"display": {"entity": {"label_field": 3}}}, "display.entity.label_field"),
        ({"display": {"visit_fields": [{"field": "o", "values": ["met"]}]}}, "visit_fields[0].values"),
        ({"display": {"visit_fields": [{"field": "o", "values": {}}]}}, "visit_fields[0].values"),
        ({"display": {"visit_fields": [{"field": "o", "values": {"met": 3}}]}}, "visit_fields[0].values.met"),
        ({"display": {"visit_fields": [{"field": "o", "values": {"met": " "}}]}}, "visit_fields[0].values.met"),
        ({"display": {"case_fields": [{"field": "o", "values": {1: "Yes"}}]}}, "case_fields[0].values"),
        ({"display": {"case_fields": [{"field": "o", "values": {"x" * 121: "X"}}]}}, "case_fields[0].values"),
        (
            {"display": {"case_fields": [{"field": "o", "values": {f"v{i}": "V" for i in range(101)}}]}},
            "at most 100",
        ),
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


def test_visit_flags_and_fields_resolve_and_default_empty(vq):
    props, inds = vq
    assert resolve_display(props, inds)["visit_flags"] == []
    assert resolve_display(props, inds)["visit_fields"] == []
    doc = {
        **copy.deepcopy(inds),
        "display": {
            "visit_fields": [{"field": "male_attendance", "label": "Men", "format": "count"}],
            "visit_flags": [
                {"column": "repeat_counts_flag", "label": "Repeat count"},
                {"column": "risk", "value": "high"},
            ],
        },
    }
    assert validate_registry(props, doc, {}) == []
    d = resolve_display(props, doc)
    assert d["visit_fields"] == [{"field": "male_attendance", "label": "Men", "format": "count"}]
    assert d["visit_flags"] == [
        {"column": "repeat_counts_flag", "label": "Repeat count"},
        {"column": "risk", "label": "risk", "value": "high"},
    ]


def test_a_visit_flag_names_the_fields_it_is_about_and_explains_itself(vq):
    """`fields` and `description` reach the page: the worker review marks the named
    cells on a flagged visit that repeat the previous one, and the Flags tooltip
    reads the description -- instead of guessing either from the flag's wording."""
    props, inds = vq
    flag = {
        "column": "repeat_counts_flag",
        "label": "Repeat count",
        "fields": ["male_attendance", "female_attendance"],
        "description": "Attendance counts exactly repeat the previous visit",
    }
    doc = {**copy.deepcopy(inds), "display": {"visit_flags": [flag]}}
    assert validate_registry(props, doc, {}) == []
    assert resolve_display(props, doc)["visit_flags"] == [flag]


def test_a_case_label_field_resolves_and_keeps_the_models_nouns(vq):
    """`display.entity.label_field` names the case-index field holding a case's human
    name. Declared alone, the entity keeps the model's nouns; undeclared, the entity
    block is exactly what it was, so saved runs and older registries are unchanged."""
    props, inds = vq
    assert "label_field" not in resolve_display(props, inds)["entity"]
    doc = {**copy.deepcopy(inds), "display": {"entity": {"label_field": "entity_name"}}}
    assert validate_registry(props, doc, {}) == []
    assert resolve_display(props, doc)["entity"] == {
        "name": "beneficiary",
        "plural": "beneficiaries",
        "key": "entity_id",
        "label_field": "entity_name",
    }
    named = {**copy.deepcopy(inds), "display": {"entity": {"name": "child", "label_field": "child_name"}}}
    assert validate_registry(props, named, {}) == []
    assert resolve_display(props, named)["entity"]["label_field"] == "child_name"


def test_a_done_property_resolves_and_must_name_a_bool_property(vq):
    """`display.entity.done_property` names the Layer-2 bool that says a case's work is
    finished. Undeclared, the entity block is exactly what it was."""
    props, inds = vq
    assert "done_property" not in resolve_display(props, inds)["entity"]
    doc = {**copy.deepcopy(inds), "display": {"entity": {"done_property": "all_approved"}}}
    assert validate_registry(props, doc, {}) == []
    entity = resolve_display(props, doc)["entity"]
    # declared alone, it keeps the model's nouns
    assert entity == {
        "name": "beneficiary",
        "plural": "beneficiaries",
        "key": "entity_id",
        "done_property": "all_approved",
    }
    both = {
        **copy.deepcopy(inds),
        "display": {"entity": {"name": "child", "label_field": "entity_name", "done_property": "all_approved"}},
    }
    assert validate_registry(props, both, {}) == []
    assert resolve_display(props, both)["entity"]["done_property"] == "all_approved"


@pytest.mark.parametrize(
    "name, message",
    [
        ("finished", "'finished' is not a property"),
        ("num_visits", "is not a property"),  # an aggregate, not a property
        ("days_since_last_visit", "must be type: bool"),  # a property, but not a bool
        ("not a name", "must be the name of a bool property"),
        (3, "must be the name of a bool property"),
    ],
)
def test_a_done_property_that_is_not_a_bool_property_is_refused(vq, name, message):
    props, inds = vq
    doc = {**copy.deepcopy(inds), "display": {"entity": {"done_property": name}}}
    errors = validate_registry(props, doc, {})
    assert any("display.entity.done_property" in e and message in e for e in errors), errors
    # without the properties document only the shape is checked
    shape_only = display_problems(doc)
    assert bool(shape_only) == (not isinstance(name, str) or " " in name)


# ── A field's stored-value -> label map (#2578) ────────────────────────────────

OUTCOMES = {"met": "Met her", "not_home": "Not at home", "left": "Left the programme"}


def test_a_fields_value_map_validates_and_resolves_into_the_display(vq):
    props, inds = vq
    doc = {
        **copy.deepcopy(inds),
        "display": {
            "case_fields": [{"field": "status", "label": "Status", "values": {"active": "Active"}}],
            "visit_fields": [{"field": "visit_outcome", "label": "Outcome", "values": OUTCOMES}],
        },
    }
    assert validate_registry(props, doc, {}) == []
    d = resolve_display(props, doc)
    assert d["visit_fields"] == [{"field": "visit_outcome", "label": "Outcome", "format": "text", "values": OUTCOMES}]
    assert d["case_fields"] == [
        {"field": "status", "label": "Status", "format": "text", "values": {"active": "Active"}}
    ]


def test_a_field_without_a_value_map_resolves_without_the_key(vq):
    """Control: no `values` declared, none carried -- so a snapshot of a registry
    that never uses it is byte-for-byte what it was."""
    props, inds = vq
    doc = {**copy.deepcopy(inds), "display": {"visit_fields": [{"field": "visit_outcome"}]}}
    assert "values" not in resolve_display(props, doc)["visit_fields"][0]


def test_resolve_keeps_only_the_string_pairs_of_a_value_map(vq):
    """The save gate refuses a bad map; a record that predates it still resolves,
    carrying only the pairs a render can print."""
    props, inds = vq
    doc = {
        **copy.deepcopy(inds),
        "display": {"visit_fields": [{"field": "o", "values": {"met": "Met her", "x": 3, 1: "One"}}]},
    }
    assert resolve_display(props, doc)["visit_fields"][0]["values"] == {"met": "Met her"}


# ── display.worker_filters ───────────────────────────────────────────────────

# A per-worker flag indicator built for a worker filter, as a programme adds one:
# the count of the worker's cases that carry a property, with `min_denominator: 1`
# so a worker with one case still has a value, and kept off the tiles and the
# scorecard WITHOUT switching the rest of the registry out of its defaults.
FLAG_MEASURES = [
    {
        "name": "in_group",
        "title": "In the reviewed group",
        "type": "number",
        "sql": "{in_group_numerator}",
        "meta": {
            "indicator": "GRP",
            "category": "Reach",
            "unit": "n",
            "direction": "none",
            "flw_applicable": True,
            "min_denominator": 1,
            "scorecard": False,
            "headline": False,
        },
    },
    {"name": "in_group_numerator", "type": "count", "filters": [{"sql": "{CUBE}.seen_recently"}]},
    {"name": "in_group_denominator", "type": "count"},
]


def _with_flag(inds, worker_filters=None):
    doc = copy.deepcopy(inds)
    doc["measures"] = doc["measures"] + copy.deepcopy(FLAG_MEASURES)
    if worker_filters is not None:
        doc["display"] = {**(doc.get("display") or {}), "worker_filters": worker_filters}
    return doc


def test_a_registry_without_worker_filters_resolves_without_the_key(vq):
    props, inds = vq
    assert "worker_filters" not in resolve_display(props, inds)


def test_a_worker_filter_validates_and_resolves_by_indicator_id_or_measure_name(vq):
    props, inds = vq
    doc = _with_flag(
        inds,
        [
            {"indicator": "in_group", "label": "  Reviewed group only "},
            {"indicator": "Q04", "label": "Every visit approved", "default": True},
        ],
    )
    assert validate_registry(props, doc, {}) == []
    d = resolve_display(props, doc)
    assert d["worker_filters"] == [
        {"indicator": "GRP", "label": "Reviewed group only", "default": False},
        {"indicator": "Q04", "label": "Every visit approved", "default": True},
    ]


def test_a_flag_measure_stays_off_the_tiles_and_scorecard_without_moving_the_others(vq):
    """`scorecard: false` / `headline: false` on the flag exclude IT alone: the
    registry's other indicators keep their prominence defaults."""
    props, inds = vq
    before = resolve_display(props, inds)
    after = resolve_display(props, _with_flag(inds, [{"indicator": "GRP", "label": "Reviewed group only"}]))
    assert after["indicators"]["GRP"]["scorecard"] is False and after["indicators"]["GRP"]["headline"] is None
    assert after["headline"] == before["headline"] == ["Q01", "Q02", "Q03"]
    on = [i for i, x in after["indicators"].items() if x["scorecard"]]
    assert on == [i for i, x in before["indicators"].items() if x["scorecard"]] == ["Q01", "Q02", "Q03"]


def test_headline_false_keeps_an_indicator_off_the_default_tiles():
    """No indicator declares a prominence or a headline: the first N are the
    tiles, except one that says `headline: false` -- and saying so does not switch
    the registry into declared-headline mode."""
    measures = [
        {"name": "a", "title": "A", "meta": {"indicator": "a"}},
        {"name": "flag", "title": "F", "meta": {"indicator": "flag", "headline": False, "scorecard": False}},
        {"name": "b", "title": "B", "meta": {"indicator": "b"}},
    ]
    shown = indicator_display(measures)
    assert [i for i in ("a", "flag", "b") if shown[i]["headline"]] == ["a", "b"]
    assert (shown["a"]["headline"], shown["b"]["headline"]) == (1, 2)
    assert [i for i in ("a", "flag", "b") if shown[i]["scorecard"]] == ["a", "b"]


@pytest.mark.parametrize(
    "worker_filters, message",
    [
        ("GRP", "display.worker_filters: must be a list"),
        (["GRP"], "display.worker_filters[0]: must be"),
        ([{"label": "Group only"}], "worker_filters[0]: needs `indicator`"),
        ([{"indicator": "nope", "label": "Group only"}], "'nope' is not an indicator of this registry"),
        ([{"indicator": "in_group_numerator", "label": "x"}], "is not an indicator of this registry"),
        ([{"indicator": "GRP"}], "worker_filters[0]: needs `label`"),
        ([{"indicator": "GRP", "label": "  "}], "worker_filters[0]: needs `label`"),
        ([{"indicator": "GRP", "label": "x" * 81}], "worker_filters[0].label: at most 80"),
        ([{"indicator": "GRP", "label": "x", "default": "yes"}], "worker_filters[0].default"),
        ([{"indicator": "GRP", "label": "x", "colour": "red"}], "worker_filters[0]: unknown key"),
        (
            [{"indicator": "GRP", "label": "x"}, {"indicator": "in_group", "label": "y"}],
            "worker_filters[1].indicator: 'in_group' already has a filter",
        ),
        ([{"indicator": "Q01", "label": f"x{i}"} for i in range(6)], "at most 5 filters"),
    ],
)
def test_a_malformed_worker_filter_is_refused(vq, worker_filters, message):
    props, inds = vq
    errors = validate_registry(props, _with_flag(inds, worker_filters), {})
    assert any(message in e for e in errors), errors


def test_resolve_drops_a_worker_filter_the_save_gate_would_refuse(vq):
    """A record that predates the gate still resolves, carrying only filters a
    render can apply."""
    props, inds = vq
    doc = _with_flag(
        inds,
        [{"indicator": "nope", "label": "x"}, {"indicator": "GRP"}, "GRP", {"indicator": "GRP", "label": "Ok"}],
    )
    assert resolve_display(props, doc)["worker_filters"] == [{"indicator": "GRP", "label": "Ok", "default": False}]


def test_the_payload_carries_the_worker_filters_it_was_built_with(vq):
    """Live preview and a saved run both build through `snapshot.build`, which
    stores the resolved display: a saved run keeps the filters it was saved with."""
    from connect_labs.semantic import snapshot as snap

    props, inds = vq
    display = resolve_display(props, _with_flag(inds, [{"indicator": "GRP", "label": "Reviewed group only"}]))
    out = snap.build(spec={}, rows=[], measures=[], deployment={}, display=display)
    assert out["display"]["worker_filters"] == [{"indicator": "GRP", "label": "Reviewed group only", "default": False}]
