"""A workflow can REFERENCE a pipeline that lives in another scope, instead of copying it.

Reads are an exact scope match, and synthetic (labs-only) records live in labs' own
store while real ones live in production Connect -- so a workflow could only use
pipelines owned in its own scope. "The same pipeline" across the real KMC report and
its synthetic twin was therefore a copy (19776 vs 5108), and every fix to the baby
key, the valid-visit rule and the grouping had to be made twice, on 2026-09-11
by hand. A pipeline source may now name its record's home scope; every read of a
workflow's pipelines honours it, and it runs over each opportunity's own data.
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.workflow.data_access import PipelineDataAccess, pipeline_homes_for

SOURCES = [
    {"pipeline_id": 19776, "alias": "children", "home_scope": {"opportunity_id": 523}},
    {"pipeline_id": 5109, "alias": "visits"},
]


class TestHomes:
    def test_only_sources_naming_a_home_are_mapped(self):
        assert pipeline_homes_for(SOURCES) == {19776: {"opportunity_id": 523}}

    def test_unknown_keys_and_nulls_are_ignored(self):
        assert pipeline_homes_for([{"pipeline_id": 1, "home_scope": {"workspace": 3, "program_id": None}}]) == {}

    def test_nothing_in_nothing_out(self):
        assert pipeline_homes_for(None) == {} and pipeline_homes_for([]) == {}


class TestTheReadHappensWhereThePipelineLives:
    def _access(self):
        pda = PipelineDataAccess.__new__(PipelineDataAccess)
        pda.labs_api = MagicMock()
        return pda

    def test_a_referenced_pipeline_is_read_in_its_home_scope(self):
        pda = self._access()
        pda.use_sources(SOURCES)
        pda.get_definition(19776)
        assert pda.labs_api.get_record_by_id.call_args.kwargs["opportunity_id"] == 523

    def test_a_pipeline_with_no_home_is_read_exactly_as_before(self):
        pda = self._access()
        pda.use_sources(SOURCES)
        pda.get_definition(5109)
        kwargs = pda.labs_api.get_record_by_id.call_args.kwargs
        assert not {"opportunity_id", "program_id", "organization_id"} & set(kwargs)

    def test_an_accessor_that_was_never_told_about_sources_is_unchanged(self):
        pda = self._access()
        pda.get_definition(19776)
        assert "opportunity_id" not in pda.labs_api.get_record_by_id.call_args.kwargs

    def test_homes_are_per_accessor_not_shared_between_instances(self):
        a, b = self._access(), self._access()
        a.use_sources(SOURCES)
        b.get_definition(19776)
        assert "opportunity_id" not in b.labs_api.get_record_by_id.call_args.kwargs


@pytest.mark.django_db
class TestAHomeOverrideReachesProductionFromASyntheticScope:
    """The client routes a labs-only scope to the local store; a home override that
    names a REAL opportunity must go to production Connect instead."""

    def test_the_override_opportunity_decides_the_store(self, monkeypatch):
        from connect_labs.labs.integrations.connect import api_client as ac

        client = ac.LabsRecordAPIClient.__new__(ac.LabsRecordAPIClient)
        client.base_url = "https://connect.example"
        client.opportunity_id, client.program_id, client.organization_id = 10042, None, None
        local = MagicMock()
        monkeypatch.setattr(ac, "_local_backend", local)
        local.is_labs_only_opportunity_id.side_effect = lambda o: o is not None and o >= 10000
        local.is_labs_only_program_id.return_value = False
        response = MagicMock()
        response.json.return_value = [
            {"id": 19776, "experiment": "pipeline", "type": "pipeline_definition", "data": {}, "opportunity_id": 523}
        ]
        client.http_client = MagicMock()
        client.http_client.get.return_value = response

        client.get_record_by_id(19776, experiment="pipeline", type="pipeline_definition", opportunity_id=523)
        local.get_record_by_id.assert_not_called()
        assert client.http_client.get.call_args.kwargs["params"]["opportunity_id"] == 523


class TestEveryWorkflowReadHonoursTheSources:
    def test_the_semantic_layer_reads_the_referenced_entity_pipeline(self):
        from connect_labs.semantic.workflow_binding import build_evaluate_inputs

        definition = MagicMock(pipeline_sources=SOURCES)
        access = MagicMock()
        access.get_definition.return_value = None
        with pytest.raises(Exception):
            build_evaluate_inputs(definition, lambda: access)
        access.use_sources.assert_called_once_with(SOURCES)

    def test_the_visit_cache_hands_the_sources_to_its_pipeline_runs(self):
        from connect_labs.workflow import visit_cache as vc

        seen = {}

        def factory(access_token, owner_scope, sources=None):
            seen["sources"] = sources
            mgr = MagicMock()
            mgr.return_value.get_raw_visit_count.return_value = 1
            return mgr, lambda o, p: 1, lambda o, p: {"rows": [], "metadata": {}}

        definition = MagicMock(opportunity_ids=[10042], pipeline_sources=SOURCES, data={})
        dao = MagicMock(access_token="t")
        dao.get_definition.return_value = definition
        vc.ensure_visit_cache(dao, 5456, opportunity_id=10042, slot_factory=factory)
        assert seen["sources"] == SOURCES


class TestAddingASourceRecordsItsHome:
    def _wda(self, sources):
        from connect_labs.workflow.data_access import WorkflowDataAccess

        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        definition = MagicMock(data={"pipeline_sources": [dict(s) for s in sources]})
        wda.get_definition = MagicMock(return_value=definition)
        wda.update_definition = MagicMock(
            side_effect=lambda did, data: MagicMock(pipeline_sources=data["pipeline_sources"])
        )
        return wda

    def test_a_home_is_stored_on_the_source(self):
        wda = self._wda([])
        out = wda.add_pipeline_source(5456, 19776, "children", home_scope={"opportunity_id": 523})
        assert out.pipeline_sources == [
            {"pipeline_id": 19776, "alias": "children", "home_scope": {"opportunity_id": 523}}
        ]

    def test_re_pointing_an_alias_keeps_its_position_and_drops_a_stale_home(self):
        wda = self._wda(
            [
                {"pipeline_id": 5108, "alias": "children", "home_scope": {"opportunity_id": 1}},
                {"pipeline_id": 5109, "alias": "visits"},
            ]
        )
        out = wda.add_pipeline_source(5456, 5110, "children")
        assert out.pipeline_sources == [
            {"pipeline_id": 5110, "alias": "children"},
            {"pipeline_id": 5109, "alias": "visits"},
        ]
