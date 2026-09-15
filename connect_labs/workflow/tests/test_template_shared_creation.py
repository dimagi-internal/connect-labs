"""Creating an instance that owns as little as possible.

A fan-out (`benchmarks_create_opp_reports`) makes one instance of a template per
opportunity in a cohort. Twelve instances that each own their render, their
pipeline records and their registry binding is twelve things to keep in step,
and this repo has already paid for each of those drifting. Two creation
arguments are what let the fan-out avoid it, and they are pinned here with a
throwaway template so the test runs with no network and no KMC coupling:

  `render_source`              the instance FOLLOWS the deployed template's
                               render, so one deploy updates them all.
  `pipeline_sources_override`  the instance REFERENCES pipeline records that
                               already exist (normally with a `home_scope`
                               naming where they live) and creates none.
"""

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

KEY = "__tv_shared_creation__"

PIPELINES = [
    {"alias": "children", "name": "Children", "schema": {"fields": []}},
    {"alias": "visits", "name": "Visits", "schema": {"fields": []}},
]

SHARED_SOURCES = [
    {"pipeline_id": 19776, "alias": "children", "home_scope": {"program_id": 46}},
    {"pipeline_id": 19777, "alias": "visits", "home_scope": {"program_id": 46}},
]


@pytest.fixture
def registry():
    TEMPLATES[KEY] = {
        "key": KEY,
        "name": KEY,
        "description": "d",
        "definition": {"name": KEY, "description": "d", "statuses": [], "config": {"keep": "me"}},
        "render_code": "function X(){return null}",
        "pipeline_schemas": PIPELINES,
    }
    yield
    TEMPLATES.pop(KEY, None)


def _data_access(opportunity_id=42):
    dao = MagicMock()
    dao.access_token = "tok"
    dao.opportunity_id = opportunity_id
    dao.program_id = None
    dao.organization_id = None

    def create_definition(name, description, **kwargs):
        rec = MagicMock()
        rec.id = 100
        rec.name = name
        rec.data = {"name": name, "description": description, **kwargs}
        return rec

    dao.create_definition.side_effect = create_definition
    return dao


def _pipeline_access():
    pda = MagicMock()
    ids = iter(range(500, 600))

    def create_definition(name, description, schema):
        rec = MagicMock()
        rec.id = next(ids)
        return rec

    pda.create_definition.side_effect = create_definition
    return pda


def _create(dao, **kwargs):
    pda = _pipeline_access()
    with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=pda):
        create_workflow_from_template(dao, KEY, request=None, **kwargs)
    return dao.create_definition.call_args.kwargs, pda


@pytest.mark.usefixtures("registry")
def test_by_default_it_creates_its_own_pipelines_and_its_own_render():
    """The control. Without it, the two assertions below cannot tell a working
    override apart from a template that never creates pipelines anyway."""
    kwargs, pda = _create(_data_access())
    assert pda.create_definition.call_count == 2
    assert [s["alias"] for s in kwargs["pipeline_sources"]] == ["children", "visits"]
    assert all("home_scope" not in s for s in kwargs["pipeline_sources"])
    assert "render_source" not in kwargs


@pytest.mark.usefixtures("registry")
def test_an_override_references_the_shared_records_and_creates_none():
    kwargs, pda = _create(_data_access(), pipeline_sources_override=SHARED_SOURCES)
    assert pda.create_definition.call_count == 0, "a copy of each pipeline was created anyway"
    assert kwargs["pipeline_sources"] == SHARED_SOURCES


@pytest.mark.usefixtures("registry")
def test_the_override_is_copied_so_twelve_instances_cannot_edit_one_list():
    """Every instance of a fan-out is handed the SAME list object; storing it
    by reference would let one definition's later edit reach all of them."""
    kwargs, _pda = _create(_data_access(), pipeline_sources_override=SHARED_SOURCES)
    kwargs["pipeline_sources"][0]["alias"] = "MUTATED"
    assert SHARED_SOURCES[0]["alias"] == "children"


@pytest.mark.usefixtures("registry")
def test_render_source_reaches_the_definition():
    kwargs, _pda = _create(_data_access(), render_source={"template": KEY})
    assert kwargs["render_source"] == {"template": KEY}
