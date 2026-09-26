"""The generic indicator cascade: KMC's drill for any registry, with no page code.

`indicator_programme_report` -> companion `indicator_worker_review`, handing its
saved weeks down to `indicator_opp_report`. These pin the wiring (registration,
companion, hand-down flags, following the deployed template, one registry record
for both pages of the drill) and the builder defaults that let one template serve
any registry.
"""

from unittest.mock import MagicMock, patch

from connect_labs.semantic import snapshot as snap
from connect_labs.semantic.model import resolve_model
from connect_labs.semantic.runtime import load_registry
from connect_labs.workflow.snapshot_builders import BUILDER_SPEC_KEYS, resolve_spec_defaults
from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template, list_templates

from .test_template_companions import _data_access, _pipeline_access

KEYS = ("indicator_programme_report", "indicator_worker_review", "indicator_opp_report")


def test_the_trio_is_registered_and_listed():
    listed = {t["key"]: t for t in list_templates()}
    for key in KEYS:
        assert key in listed, key
    assert listed["indicator_programme_report"]["companions"] == ["indicator_worker_review"]
    assert listed["indicator_worker_review"]["companion_of"] == ["indicator_programme_report"]
    assert listed["indicator_programme_report"]["supports_saved_runs"]
    assert listed["indicator_opp_report"]["supports_saved_runs"]


def test_hand_down_wiring():
    assert TEMPLATES["indicator_programme_report"]["hands_down_to_opportunity_reports"] is True
    assert TEMPLATES["indicator_opp_report"]["receives_hand_down"] is True
    assert "source_workflow_id" in TEMPLATES["indicator_opp_report"]["definition"]["config"]
    assert TEMPLATES["indicator_programme_report"]["multi_opp"] is True
    assert TEMPLATES["indicator_opp_report"]["multi_opp"] is False


def test_the_spec_names_only_the_builder_so_the_registry_decides_the_rest():
    for key in ("indicator_programme_report", "indicator_opp_report"):
        spec = TEMPLATES[key]["snapshot_inputs"]
        assert spec["builder"] == "semantic_snapshot"
        extra = set(spec) - {"builder", "workers", "pipelines"} - BUILDER_SPEC_KEYS["semantic_snapshot"]
        assert not extra


def test_no_programme_specific_word_in_the_renders():
    from pathlib import Path

    root = Path(__file__).parents[1] / "templates"
    for name in ("indicator_report_render.js", "indicator_worker_review_render.js"):
        # Code, not comments: a comment may say what this replaced.
        src = "\n".join(
            line for line in (root / name).read_text().lower().splitlines() if not line.strip().startswith("//")
        )
        for word in ("kmc", "baby", "babies", "llo_of", "mortality", "weight_g", "started_cases"):
            assert word not in src, f"{name} carries programme-specific {word!r}"


def _create(dao, key, **kw):
    registry_access = MagicMock()
    registry_access.create_registry.return_value = MagicMock(id=7777)
    with (
        patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=_pipeline_access()),
        patch("connect_labs.workflow.data_access.SemanticRegistryDataAccess", return_value=registry_access),
    ):
        return create_workflow_from_template(dao, key, request=None, **kw)


class TestOneCreateBuildsTheDrill:
    def test_the_report_follows_the_template_and_links_its_companion(self):
        dao = _data_access()
        definition, _r, _p = _create(dao, "indicator_programme_report", opportunity_ids=[10013, 10014])
        created = [c.kwargs for c in dao.create_definition.call_args_list]
        assert [c["config"]["templateType"] for c in created] == [
            "indicator_programme_report",
            "indicator_worker_review",
        ]
        # Both follow the deployed template.
        assert [c.get("render_source") for c in created] == [
            {"template": "indicator_programme_report"},
            {"template": "indicator_worker_review"},
        ]
        # The companion shares the primary's pipeline records and is linked both ways.
        assert created[1]["pipeline_sources"] == created[0]["pipeline_sources"]
        assert created[1]["config"]["source_workflow_id"] == 100
        assert definition.data["config"]["worker_review"] == {"workflow_id": 101, "run_id": 900}

    def test_a_named_registry_is_bound_and_default_seed_is_visit_quality(self):
        dao = _data_access()
        definition, _r, _p = _create(
            dao, "indicator_programme_report", registry_source={"registry_id": 19784, "public": True}
        )
        assert definition.data["registry_source"] == {"registry_id": 19784, "public": True}
        assert TEMPLATES["indicator_programme_report"]["semantic_registry"] == "visit_quality"

    def test_referenced_pipelines_replace_the_default_ones(self):
        dao = _data_access()
        shared = [{"pipeline_id": 19776, "alias": "children", "home_scope": {"public": True}}]
        _create(dao, "indicator_programme_report", pipeline_sources_override=shared)
        created = [c.kwargs for c in dao.create_definition.call_args_list]
        assert created[0]["pipeline_sources"] == shared == created[1]["pipeline_sources"]

    def test_the_opportunity_report_names_its_source_at_create(self):
        dao = _data_access()
        definition, _r, _p = _create(dao, "indicator_opp_report", config_overrides={"source_workflow_id": 555})
        assert definition.data["config"]["source_workflow_id"] == 555
        assert definition.data["render_source"] == {"template": "indicator_opp_report"}

    def test_kmc_templates_are_not_followed_by_default(self):
        assert not TEMPLATES["kmc_programme_metrics"].get("follow_template")
        assert not TEMPLATES["kmc_opp_report"].get("follow_template")


class TestBuilderDefaults:
    def test_visit_quality_derives_a_grouped_case_index_and_no_llo_scopes(self):
        props, inds = load_registry("visit_quality")
        model = resolve_model(props, inds)
        visit_cfg = MagicMock(terminal_stage=MagicMock(value="visit_level"))
        spec = resolve_spec_defaults({"builder": "semantic_snapshot"}, model, visit_cfg, None, {}, inds)
        assert "llo" not in spec["scopes"] and "llo_month" not in spec["scopes"]
        assert spec["scopes"][0] == "programme" and "flw" in spec["scopes"]
        assert spec["visits_pipeline"] == "visits"
        assert spec["case_index"]["pipeline"] == "visits"
        assert spec["case_index"]["group_by"] == "entity_id"
        assert spec["maturity_anchor"] == "first_visit_date"

    def test_a_stated_spec_is_kept_as_written(self):
        from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS

        props, inds = load_registry("kmc")
        model = resolve_model(props, inds)
        entity_cfg = MagicMock(terminal_stage=MagicMock(value="entity"))
        out = resolve_spec_defaults(dict(SNAPSHOT_INPUTS), model, entity_cfg, None, {10013: "Kikapu"}, inds)
        for k in ("scopes", "case_index", "visits_pipeline", "maturity_anchor"):
            assert out[k] == SNAPSHOT_INPUTS[k]
        assert out["credibility"]["mortality"] == "mortality_recording_credible"

    def test_an_entity_registry_takes_its_visit_pipeline_from_the_extra_fields(self):
        props, inds = load_registry("kmc")
        model = resolve_model(props, inds)
        entity_cfg = MagicMock(terminal_stage=MagicMock(value="entity"))
        visit_cfg = MagicMock(terminal_stage=MagicMock(value="visit_level"))
        spec = resolve_spec_defaults({}, model, entity_cfg, {"weight_g": visit_cfg}, {10013: "Kikapu"}, inds)
        assert spec["visits_pipeline"] == "visits"
        assert spec["case_index"]["pipeline"] == "children" and "group_by" not in spec["case_index"]
        assert "llo" in spec["scopes"]


def test_a_visit_level_case_index_is_folded_per_entity():
    visits = [
        {"entity_id": "a", "opportunity_id": 1, "username": "w1", "visit_date": "2026-01-05"},
        {"entity_id": "a", "opportunity_id": 1, "username": "w1", "visit_date": "2026-01-01"},
        {"entity_id": "b", "opportunity_id": 1, "username": "w2", "visit_date": "2026-01-03"},
        {"entity_id": None, "opportunity_id": 1, "username": "w2", "visit_date": "2026-01-03"},
    ]
    spec = {
        "case_index": {
            "pipeline": "visits",
            "group_by": "entity_id",
            "fields": [
                "entity_id",
                "username",
                "opportunity_id",
                "first_visit_date",
                "last_visit_date",
                "total_visits",
            ],
        }
    }
    rows = snap.case_rows({"visits": {"rows": visits}}, spec, {1: "Org"})
    by = {r["entity_id"]: r for r in rows}
    assert set(by) == {"a", "b"}
    assert by["a"]["total_visits"] == 2
    assert (by["a"]["first_visit_date"], by["a"]["last_visit_date"]) == ("2026-01-01", "2026-01-05")
    assert by["a"]["username"] == "w1" and by["a"]["llo"] == "Org"


def test_the_payload_carries_the_display_block_and_the_kmc_one_is_unchanged_without_it():
    out = snap.build(spec={}, rows=[], measures=[], deployment={}, display={"entity": {"name": "x"}})
    assert out["display"] == {"entity": {"name": "x"}}
    assert "display" not in snap.build(spec={}, rows=[], measures=[], deployment={})
