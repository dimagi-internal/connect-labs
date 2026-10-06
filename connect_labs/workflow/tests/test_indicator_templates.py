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

    def test_a_declared_case_label_field_joins_the_derived_case_index(self):
        import copy

        props, inds = load_registry("visit_quality")
        inds = {**copy.deepcopy(inds), "display": {"entity": {"label_field": "entity_name"}}}
        model = resolve_model(props, inds)
        visit_cfg = MagicMock(terminal_stage=MagicMock(value="visit_level"))
        spec = resolve_spec_defaults({}, model, visit_cfg, None, {}, inds)
        fields = spec["case_index"]["fields"]
        assert fields.count("entity_name") == 1 and fields[0] == "entity_id"

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


def test_a_folded_case_carries_its_latest_name():
    visits = [
        {
            "entity_id": "a",
            "entity_name": "Grace M.",
            "opportunity_id": 1,
            "username": "w1",
            "visit_date": "2026-01-01",
        },
        {"entity_id": "a", "entity_name": "", "opportunity_id": 1, "username": "w1", "visit_date": "2026-01-05"},
    ]
    spec = {"case_index": {"pipeline": "visits", "group_by": "entity_id", "fields": ["entity_id", "entity_name"]}}
    (row,) = snap.case_rows({"visits": {"rows": visits}}, spec, {})
    assert row["entity_name"] == "Grace M."


class TestWorkerNames:
    """A worker reads by name; the username stays the identity."""

    def _rows(self):
        return [
            {"scope": "flw", "opportunity_id": 10_082, "username": "cbf_a07", "n_cases": 3},
            {"scope": "flw", "opportunity_id": 10_082, "username": "cbf_a08", "n_cases": 1},
            {"scope": "flw", "opportunity_id": 10_083, "username": "cbf_a07", "n_cases": 2},
        ]

    def test_each_worker_row_carries_its_name_beside_its_identity(self):
        names = {"10082": {"cbf_a07": "Amina Okafor"}, "10083": {"cbf_a07": "Somebody Else"}}
        out = snap.build(spec={}, rows=self._rows(), measures=[], deployment={}, worker_names=names)
        by = {f["key"]: f for f in out["byFLW"]}
        a07 = by[f"10082{snap.FLW_SEP}cbf_a07"]
        assert a07["name"] == "Amina Okafor"
        # identity untouched: selection, audits and URLs key on these
        assert a07["flw"] == "cbf_a07" and a07["username"] == "cbf_a07"
        # the same username in another opportunity is a different worker with its own name
        assert by[f"10083{snap.FLW_SEP}cbf_a07"]["name"] == "Somebody Else"
        # no name known: the username, as before
        assert by[f"10082{snap.FLW_SEP}cbf_a08"]["name"] == "cbf_a08"

    def test_without_names_a_run_reads_as_it_always_did(self):
        out = snap.build(spec={}, rows=self._rows(), measures=[], deployment={})
        assert all(f["name"] == f["username"] for f in out["byFLW"])

    def test_names_are_resolved_per_opportunity_and_a_failure_is_not_fatal(self):
        from connect_labs.workflow.snapshot_builders import worker_names

        def fake(token, oid, **kw):
            if oid == 2:
                raise RuntimeError("Connect is down")
            return {"u1": "Amina Okafor", "u2": "u2", "u3": ""}

        with patch("connect_labs.labs.analysis.data_access.fetch_flw_names", side_effect=fake) as f:
            out = worker_names([1, 2, 1], access_token="tok")
        # a name equal to the username, or blank, is no name; a failed opportunity is skipped
        assert out == {"1": {"u1": "Amina Okafor"}}
        assert sorted(c.args[1] for c in f.call_args_list) == [1, 2]
        assert f.call_args_list[0].args[0] == "tok"

    def test_with_no_token_only_a_fixture_backed_opportunity_is_asked(self):
        from connect_labs.workflow.snapshot_builders import worker_names

        with (
            patch("connect_labs.labs.analysis.data_access.fetch_flw_names", return_value={"u1": "Amina"}) as f,
            patch(
                "connect_labs.labs.synthetic.registry.get_synthetic_opp",
                side_effect=lambda oid: object() if oid == 10_082 else None,
            ),
        ):
            out = worker_names([814, 10_082])
        assert out == {"10082": {"u1": "Amina"}}
        assert [c.args[1] for c in f.call_args_list] == [10_082]

    def test_a_synthetic_roster_names_its_workers_from_the_manifest_personas(self, db):
        """A labs-only opp is served its fixture user_data, whose `name` is the persona's
        display_name -- so the same resolver names synthetic workers with no token."""
        from connect_labs.labs.synthetic.client import SyntheticExportClient
        from connect_labs.workflow.snapshot_builders import worker_names

        store = MagicMock()
        store.load_endpoint.return_value = [
            {"username": "cbf_a07", "name": "Amina Okafor"},
            {"username": "cbf_a08", "name": "cbf_a08"},
        ]
        client = SyntheticExportClient(opp_id=10_082, fixture_store=store)
        with (
            patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client),
            patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=object()),
        ):
            out = worker_names([10_082])
        assert out == {"10082": {"cbf_a07": "Amina Okafor"}}
        store.load_endpoint.assert_called_with(10_082, "user_data")

    def test_a_handed_down_slice_carries_only_its_own_workers_names(self):
        from connect_labs.workflow.hand_down import slice_for_opportunity

        names = {"10082": {"cbf_a07": "Amina Okafor"}, "10083": {"cbf_a07": "Somebody Else"}}
        rows = self._rows() + [
            {"scope": "opportunity", "opportunity_id": 10_082, "n_cases": 4},
            {"scope": "opportunity", "opportunity_id": 10_083, "n_cases": 2},
        ]
        payload = snap.build(spec={}, rows=rows, measures=[], deployment={}, worker_names=names)
        sliced = slice_for_opportunity(payload, 10_082)
        assert {f["name"] for f in sliced["byFLW"]} == {"Amina Okafor", "cbf_a08"}
        assert "Somebody Else" not in str(sliced)

    def test_the_renders_show_the_name_and_key_on_the_username(self):
        from pathlib import Path

        root = Path(__file__).parents[1] / "templates"
        report = (root / "indicator_report_render.js").read_text()
        assert "name: f.name || f.username || f.flw" in report
        assert "key: f.key || f.opp + FLW_SEP + (f.username || f.flw)" in report
        review = (root / "indicator_worker_review_render.js").read_text()
        assert "title={flw.name || flw.flw || flw.username}" in review
        # and a case reads by its label field through the shared library
        for src in (report, review):
            assert "R.caseLabel(D, c, n)" in src


def test_the_payload_carries_the_display_block_and_the_kmc_one_is_unchanged_without_it():
    out = snap.build(spec={}, rows=[], measures=[], deployment={}, display={"entity": {"name": "x"}})
    assert out["display"] == {"entity": {"name": "x"}}
    assert "display" not in snap.build(spec={}, rows=[], measures=[], deployment={})


def test_a_program_owned_report_reads_its_seeded_registry_in_the_programs_scope(monkeypatch):
    """A seeded record lives in the workflow's own scope. For a program-owned report
    that scope has NO opportunity, so reading it through the data anchor (opp +
    program) found nothing: 'no semantic registry with id ...' on the first save of a
    generic report created over visit_quality (synthetic program 10011, 2026-09-26)."""
    from connect_labs.semantic import runtime
    from connect_labs.semantic import snapshot as snap
    from connect_labs.semantic import workflow_binding
    from connect_labs.workflow import data_access as wf_data_access
    from connect_labs.workflow.snapshot_builders import semantic_snapshot

    from .test_periodic_builders import _DAO

    scopes = []

    class _Registry(_DAO):
        def __init__(self, *a, **kw):
            scopes.append({k: v for k, v in kw.items() if k in ("opportunity_id", "program_id")})

    def resolve(definition, registry_access_factory=None):
        registry_access_factory()
        return {}, {}, {}, {}, {}, "x"

    monkeypatch.setattr(wf_data_access, "WorkflowDataAccess", _DAO)
    monkeypatch.setattr(wf_data_access, "PipelineDataAccess", _DAO)
    monkeypatch.setattr(wf_data_access, "SemanticRegistryDataAccess", _Registry)
    monkeypatch.setattr(workflow_binding, "build_evaluate_inputs", lambda d, f, **kw: ({}, {}))
    monkeypatch.setattr(workflow_binding, "resolve_registry_for", resolve)
    monkeypatch.setattr(runtime, "evaluate", lambda *a, **kw: [])
    monkeypatch.setattr(runtime, "filter_to_series", lambda reg, s: reg)
    monkeypatch.setattr(runtime, "measure_catalog", lambda reg: [])
    monkeypatch.setattr(snap, "build", lambda **kw: {"ok": True})

    semantic_snapshot(
        spec={"series": "Q", "scopes": ["programme"]},
        pipelines={},
        opportunity_id=10013,
        context={"definition_id": 1, "opportunity_ids": [10013], "program_id": 10011},
    )
    assert scopes == [{"program_id": 10011}]


def test_a_program_owned_reports_pipelines_say_where_they_live():
    """They land on the first spanned opportunity; a drill reading another
    opportunity's rows must still find the record (worker review, 2026-09-26:
    'pipeline 6353 not found')."""
    dao = _data_access(opportunity_id=None, program_id=10011)
    _create(dao, "indicator_programme_report", opportunity_ids=[10013, 10020])
    created = [c.kwargs for c in dao.create_definition.call_args_list]
    for sources in (created[0]["pipeline_sources"], created[1]["pipeline_sources"]):
        assert sources and all(s["home_scope"] == {"opportunity_id": 10013} for s in sources)


class TestOpportunityLabels:
    """A report names its opportunities: "Opportunity 10082" means nothing to a partner."""

    def _synthetic(self, oid, label):
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        SyntheticOpportunity.objects.create(opportunity_id=oid, label=label, gdrive_folder_id="f", labs_only=True)

    def test_a_labs_only_opportunity_is_named_from_its_own_record(self, db):
        from connect_labs.workflow.snapshot_builders import opportunity_labels

        self._synthetic(10_082, "[Synthetic] Spark facilitator - Partner A")
        self._synthetic(10_099, "not in this report")
        assert opportunity_labels([10_082, 10_083]) == {"10082": "[Synthetic] Spark facilitator - Partner A"}

    def test_the_registry_can_rename_one_and_only_for_opportunities_in_scope(self, db):
        from connect_labs.workflow.snapshot_builders import opportunity_labels

        self._synthetic(10_082, "own record name")
        out = opportunity_labels([10_082], declared={"10082": "Partner A site", "555": "elsewhere"})
        assert out == {"10082": "Partner A site"}

    def test_a_real_opportunity_is_named_from_the_requesters_org_data(self, db):
        from connect_labs.workflow.snapshot_builders import opportunity_labels

        with patch(
            "connect_labs.labs.context.get_org_data",
            return_value={"opportunities": [{"id": 814, "name": "KMC Kenya"}, {"id": 900, "name": "other"}]},
        ):
            assert opportunity_labels([814, 815], request=object()) == {"814": "KMC Kenya"}

    def test_the_payload_carries_them_and_the_registry_record_keeps_them(self):
        from connect_labs.semantic.runtime import normalise_deployment_facts

        facts = normalise_deployment_facts({"opportunity_labels": {10082: "Partner A site", 10083: ""}})
        assert facts["opportunity_labels"] == {"10082": "Partner A site"}
        out = snap.build(spec={}, rows=[], measures=[], deployment=facts, as_of="2026-09-20")
        assert out["deployment"]["opportunity_labels"] == {"10082": "Partner A site"}

    def test_the_render_names_opportunities_and_counts_the_drilled_scope(self):
        from pathlib import Path

        src = (Path(__file__).parents[1] / "templates" / "indicator_report_render.js").read_text()
        # Every place an opportunity is shown goes through the resolver...
        assert "'Opportunity ' + o.opp" not in src and "'Opportunity ' + selOpp" not in src
        assert "opportunity_labels" in src and "user-opportunities" in src
        # The opportunity report's page carries the list only in the header selector.
        assert "'opportunity-data'" in src
        # ...and the drilled header counts its own scope, not the programme's.
        assert "R.nounCount(scopeCases, ENT)" in src and "R.nounCount(scopeWorkers, WRK)" in src
