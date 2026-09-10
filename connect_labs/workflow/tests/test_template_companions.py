"""Companions: a template that creates its second page alongside itself.

The KMC programme report's worker rows link to a second workflow. Before
`companions`, creating the report from its template gave you a report whose
worker rows were not links, and a four-call runbook to fix that by hand. These
pin the mechanism with throwaway templates so they run with no network and no
KMC coupling: the companion is created in the same scope over the same
opportunities, shares the primary's pipeline records, gets its run minted,
and both configs are cross-linked before create returns — or, on a bad
declaration, NOTHING is created.
"""

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template, list_templates

PRIMARY = "__tv_companion_primary__"
COMPANION = "__tv_companion_second__"
THIRD = "__tv_companion_third__"


def _template(key, **extra):
    t = {
        "key": key,
        "name": key,
        "description": "d",
        "multi_opp": True,
        "definition": {"name": key, "description": "d", "statuses": [], "config": {"keep": "me"}},
        "render_code": "function X(){return null}",
    }
    t.update(extra)
    return t


PIPELINES = [
    {"alias": "children", "name": "Children", "schema": {"fields": []}},
    {"alias": "visits", "name": "Visits", "schema": {"fields": []}},
]

SPEC = {
    "template_key": COMPANION,
    "config_key": "second_page",
    "share_pipelines": True,
    "mint_run": True,
    "back_reference": "source_workflow_id",
}


@pytest.fixture
def registry():
    TEMPLATES[PRIMARY] = _template(PRIMARY, pipeline_schemas=PIPELINES, companions=[SPEC])
    TEMPLATES[COMPANION] = _template(COMPANION, pipeline_schemas=PIPELINES)
    yield
    for key in (PRIMARY, COMPANION, THIRD):
        TEMPLATES.pop(key, None)


def _data_access(opportunity_id=42, program_id=None):
    """A recording WorkflowDataAccess: definitions get ascending ids and carry
    the data they were created with, so the link write can be inspected."""
    dao = MagicMock()
    dao.access_token = "tok"
    dao.opportunity_id = opportunity_id
    dao.program_id = program_id
    dao.organization_id = None
    ids = iter(range(100, 200))

    def create_definition(name, description, **kwargs):
        rec = MagicMock()
        rec.id = next(ids)
        rec.name = name
        rec.data = {"name": name, "description": description, **kwargs}
        return rec

    def update_definition(definition_id, data):
        rec = MagicMock()
        rec.id = definition_id
        rec.name = data.get("name")
        rec.data = data
        return rec

    run = MagicMock()
    run.id = 900
    dao.create_definition.side_effect = create_definition
    dao.update_definition.side_effect = update_definition
    dao.create_run.return_value = run
    return dao


def _pipeline_access():
    pda = MagicMock()
    ids = iter(range(500, 600))

    def create_definition(name, description, schema):
        rec = MagicMock()
        rec.id = next(ids)
        rec.name = name
        return rec

    pda.create_definition.side_effect = create_definition
    return pda


@pytest.mark.usefixtures("registry")
class TestCreatesTheCompanionAlongside:
    def _create(self, dao):
        pda = _pipeline_access()
        with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=pda):
            definition, _render, _pipeline = create_workflow_from_template(
                dao, PRIMARY, request=None, opportunity_ids=[1, 2, 3]
            )
        return definition, pda

    def test_the_companion_is_created_in_the_same_scope_over_the_same_opportunities(self):
        dao = _data_access()
        self._create(dao)
        calls = dao.create_definition.call_args_list
        assert [c.kwargs["config"]["templateType"] for c in calls] == [PRIMARY, COMPANION]
        assert all(c.kwargs["opportunity_ids"] == [1, 2, 3] for c in calls)
        # Same DAO, so the same owner: nothing re-scoped the companion.
        assert dao.create_definition.call_count == 2

    def test_the_companion_shares_the_primarys_pipelines_and_creates_none(self):
        dao = _data_access()
        _definition, pda = self._create(dao)
        primary, companion = dao.create_definition.call_args_list
        assert pda.create_definition.call_count == 2, "the primary's two pipelines, and no more"
        assert companion.kwargs["pipeline_sources"] == primary.kwargs["pipeline_sources"]
        assert [s["alias"] for s in companion.kwargs["pipeline_sources"]] == ["children", "visits"]

    def test_both_sides_of_the_link_are_written(self):
        dao = _data_access()
        definition, _pda = self._create(dao)
        primary, companion = dao.create_definition.call_args_list
        primary_id = 100
        assert companion.kwargs["config"]["source_workflow_id"] == primary_id
        # The primary is updated ONCE, with the whole record plus the link — a
        # partial config would drop every other key it carries.
        dao.update_definition.assert_called_once()
        written = dao.update_definition.call_args.kwargs["data"]
        assert written["config"]["second_page"] == {"workflow_id": 101, "run_id": 900}
        assert written["config"]["keep"] == "me"
        assert written["config"]["templateType"] == PRIMARY
        assert written["name"] == PRIMARY
        # ...and the caller gets the linked record back, not the pre-link one.
        assert definition.data["config"]["second_page"]["workflow_id"] == 101

    def test_the_run_is_minted_on_the_companion_under_the_same_owner(self):
        dao = _data_access(opportunity_id=42)
        self._create(dao)
        dao.create_run.assert_called_once()
        args, kwargs = dao.create_run.call_args
        assert args == (101,)
        assert kwargs["opportunity_id"] == 42 and kwargs["program_id"] is None
        assert kwargs["period_start"] == kwargs["period_end"]

    def test_a_program_owned_primary_mints_a_program_owned_run(self):
        dao = _data_access(opportunity_id=None, program_id=176)
        with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=_pipeline_access()):
            create_workflow_from_template(dao, PRIMARY, request=None, opportunity_ids=[1, 2], program_id=176)
        _args, kwargs = dao.create_run.call_args
        assert kwargs["opportunity_id"] is None and kwargs["program_id"] == 176

    def test_the_templates_own_definition_is_not_mutated(self):
        """config is copied per create: the back reference stamped on the
        companion must not appear on the NEXT instance of that template."""
        dao = _data_access()
        self._create(dao)
        assert "source_workflow_id" not in TEMPLATES[COMPANION]["definition"]["config"]
        assert "templateType" not in TEMPLATES[COMPANION]["definition"]["config"]

    def test_list_templates_names_the_companion(self):
        row = next(t for t in list_templates() if t["key"] == PRIMARY)
        assert row["companions"] == [COMPANION]
        assert next(t for t in list_templates() if t["key"] == COMPANION)["companions"] == []


@pytest.mark.usefixtures("registry")
class TestABadDeclarationCreatesNothing:
    """Validated up front: a failure halfway would leave a primary with no link
    and a companion with no owner — the half-built state companions exist to end."""

    def _expect_nothing(self, dao, match):
        with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=_pipeline_access()) as pda:
            with pytest.raises(ValueError, match=match):
                create_workflow_from_template(dao, PRIMARY, request=None, opportunity_ids=[1])
        dao.create_definition.assert_not_called()
        dao.create_run.assert_not_called()
        pda.return_value.create_definition.assert_not_called()

    def test_unknown_companion_template(self):
        TEMPLATES[PRIMARY]["companions"] = [{**SPEC, "template_key": "__nope__"}]
        self._expect_nothing(_data_access(), "unknown companion template")

    def test_deprecated_companion_template(self):
        TEMPLATES[COMPANION]["deprecated"] = True
        self._expect_nothing(_data_access(), "deprecated")

    def test_unknown_key_in_the_declaration(self):
        TEMPLATES[PRIMARY]["companions"] = [{**SPEC, "mint_rnu": True}]
        self._expect_nothing(_data_access(), "unknown companion keys")

    def test_a_cycle_is_refused(self):
        TEMPLATES[COMPANION]["companions"] = [{"template_key": PRIMARY, "config_key": "back"}]
        self._expect_nothing(_data_access(), "cycle")

    def test_a_chain_is_allowed(self):
        """A → B → C is fine; only A → B → A is a cycle."""
        TEMPLATES[THIRD] = _template(THIRD)
        TEMPLATES[COMPANION]["companions"] = [{"template_key": THIRD, "config_key": "third"}]
        dao = _data_access()
        with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=_pipeline_access()):
            create_workflow_from_template(dao, PRIMARY, request=None, opportunity_ids=[1])
        kinds = [c.kwargs["config"]["templateType"] for c in dao.create_definition.call_args_list]
        assert kinds == [PRIMARY, COMPANION, THIRD]
