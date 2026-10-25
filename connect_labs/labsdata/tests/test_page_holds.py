"""Holding a page's agent to the page: the rules, page by page (``labsdata/page_holds.py``).

The end-to-end path (page -> mint -> /o/token/ -> MCP) is ``mcp/tests/test_page_holds_end_to_end.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from connect_labs.labsdata import page_holds
from connect_labs.labsdata.models import PageHold

RUN_PAGE = "labs:workflow:run:data"


def _opened(ids, *, space_id=None, workflow=None):
    """The space a data call resolved (``spaces.from_request``), as far as the hold reads it."""
    return SimpleNamespace(
        saved=space_id is not None,
        id=space_id,
        space=SimpleNamespace(workflow=workflow or {}),
        selection=SimpleNamespace(ids=list(ids), workflow={"id": workflow["id"]} if workflow else None),
    )


def _held(**hold):
    return mock.patch.object(page_holds, "current", return_value=PageHold(page_key=RUN_PAGE, **hold))


def test_a_persons_own_mcp_use_is_not_held():
    # No delegated token: no page, so nothing to hold to -- whatever is read.
    with mock.patch.object(page_holds, "current", return_value=None):
        page_holds.hold_to_page(_opened([1, 2, 3]))
        page_holds.hold_workflow(99)


def test_a_delegated_call_with_no_recorded_page_is_refused():
    with mock.patch.object(page_holds, "current", return_value=page_holds._NO_HOLD):
        with pytest.raises(page_holds.OutsidePage) as e:
            page_holds.hold_to_page(_opened([1]))
    assert e.value.code == "outside_page" and "did not record" in e.value.message


def test_a_run_page_reads_its_workflows_opportunities_and_pipelines():
    wf = {"id": 9027, "program_id": 10121}
    with _held(workflow=wf, opportunity_ids=[10122, 10123]):
        page_holds.hold_to_page(_opened([10122, 10123], workflow=wf))
        page_holds.hold_to_page(_opened([10123]))
        page_holds.hold_workflow(9027)


def test_a_run_page_refuses_another_programme_the_visitor_holds():
    with _held(workflow={"id": 9027, "program_id": 10121}, opportunity_ids=[10122, 10123]):
        with pytest.raises(page_holds.OutsidePage) as e:
            page_holds.hold_to_page(_opened([10122, 10061]))
    assert e.value.outside == [10061]
    assert e.value.as_dict()["reason"] == "outside_page"
    assert "10061" in e.value.message and "/labs/data/" in e.value.message
    assert e.value.page["workflow"]["id"] == 9027


def test_a_run_page_refuses_another_workflows_pipelines_and_definition():
    other = {"id": 9029, "program_id": 10121}
    with _held(workflow={"id": 9027, "program_id": 10121}, opportunity_ids=[10122, 10123]):
        # The same opportunities, through another workflow's pipelines: not this page's.
        with pytest.raises(page_holds.OutsidePage, match="9029"):
            page_holds.hold_to_page(_opened([10122, 10123], workflow=other))
        with pytest.raises(page_holds.OutsidePage, match="9029"):
            page_holds.hold_workflow(9029)


def test_a_space_page_reads_that_space_only():
    with _held(space_id=7, opportunity_ids=[10132]):
        page_holds.hold_to_page(_opened([10132], space_id=7))
        with pytest.raises(page_holds.OutsidePage, match="space_id"):
            page_holds.hold_to_page(_opened([10132], space_id=8))
        with pytest.raises(page_holds.OutsidePage):
            page_holds.hold_to_page(_opened([10132]))
        # A space with no workflow has no workflow context to read.
        with pytest.raises(page_holds.OutsidePage, match="no workflow"):
            page_holds.hold_workflow(9027)


def test_the_data_page_says_to_widen_the_selection_on_the_page():
    hold = PageHold(page_key=page_holds.DATA_PAGE, opportunity_ids=[1])
    with mock.patch.object(page_holds, "current", return_value=hold):
        with pytest.raises(page_holds.OutsidePage) as e:
            page_holds.hold_to_page(_opened([1, 2]))
    assert "add it to the selection on the Data page" in e.value.message


def test_an_empty_data_page_reads_nothing():
    hold = PageHold(page_key=page_holds.DATA_PAGE, opportunity_ids=[])
    with mock.patch.object(page_holds, "current", return_value=hold):
        with pytest.raises(page_holds.OutsidePage):
            page_holds.hold_to_page(_opened([1]))


def test_for_selection_holds_the_programs_opportunities_the_person_holds():
    held = {
        1: SimpleNamespace(id=1, program_id=10),
        2: SimpleNamespace(id=2, program_id=10),
        3: SimpleNamespace(id=3, program_id=11),
    }
    holds = page_holds.for_selection({"workflow": None, "opportunity_ids": [3, 99], "program_ids": [10]}, held)
    assert holds.opportunity_ids == (1, 2, 3)  # 99 is not held: not on the page
    wf = page_holds.for_selection(
        {"workflow": {"id": 5, "program_id": 10}, "opportunity_ids": [], "program_ids": []}, held
    )
    assert wf.workflow == {"id": 5, "program_id": 10} and wf.opportunity_ids is None


def test_the_signed_selection_is_bound_to_its_page_token_and_person():
    holds = page_holds.Holds(workflow={"id": 9027, "program_id": 10121}, opportunity_ids=(10122,))
    signed = page_holds.sign(holds, "page-token-a", 5)
    assert page_holds._unsign(signed, "page-token-a", 5) == holds
    assert page_holds._unsign(signed, "page-token-b", 5) is None
    assert page_holds._unsign(signed, "page-token-a", 6) is None
    assert page_holds._unsign(signed[:-2] + "xx", "page-token-a", 5) is None


def _run_hold(**workflow):
    return PageHold(page_key=RUN_PAGE, workflow={"id": 9027, "program_id": 10121, "run_id": 10021, **workflow})


def test_run_tools_off_a_page_are_not_held():
    with mock.patch.object(page_holds, "current", return_value=None):
        assert page_holds.run_denial("workflow_run_context", {"run_id": 1}) is None


def test_run_tools_with_no_recorded_page_are_refused():
    with mock.patch.object(page_holds, "current", return_value=page_holds._NO_HOLD):
        assert page_holds.run_denial("workflow_run_indicators", {"run_id": 10021}).code == "outside_page"


def test_run_tools_reach_the_pages_run_only():
    with mock.patch.object(page_holds, "current", return_value=_run_hold()):
        assert page_holds.run_denial("workflow_run_context", {"run_id": 10021, "program_id": 10121}) is None
        assert page_holds.run_denial("workflow_run_context", {"run_id": "10021"}) is None
        other = page_holds.run_denial("workflow_run_action", {"run_id": 10022, "action": "a", "arguments": {}})
        assert other is not None and "10022" in other.message and "/labs/data/" in other.message
        assert page_holds.run_denial("workflow_run_cases", {"run_id": 10021, "opportunity_id": 5}) is not None
        # Tools that take no run are not this check's.
        assert page_holds.run_denial("data_query", {"opportunity_ids": [1]}) is None


def test_a_page_without_a_run_reaches_no_run():
    # A Data page or space hold (its grant has no run scopes anyway): fail closed.
    hold = PageHold(page_key=page_holds.DATA_PAGE, opportunity_ids=[1])
    with mock.patch.object(page_holds, "current", return_value=hold):
        assert page_holds.run_denial("workflow_run_context", {"run_id": 10021}) is not None


def test_proposed_edits_change_only_the_pages_workflow_and_its_pipelines():
    with mock.patch.object(page_holds, "current", return_value=_run_hold(pipeline_ids=[19776, 19777])):

        def propose(tool, **arguments):
            return page_holds.run_denial("workflow_edit_propose", {"tool": tool, "arguments": arguments})

        assert propose("workflow_update_definition", workflow_id=9027, definition={}) is None
        assert propose("workflow_update_definition", workflow_id=9029, definition={}) is not None
        assert propose("workflow_patch_render_code", workflow_id=9029) is not None
        assert propose("pipeline_update_schema", pipeline_id=19776, schema={}) is None
        assert propose("pipeline_update_schema", pipeline_id=555, schema={}) is not None


def test_for_workflow_records_the_run_and_its_pipelines():
    holds = page_holds.for_workflow(9027, {"program_id": 10121}, [10122], run_id=10021, pipeline_ids=[19777, 19776])
    assert holds.workflow == {"id": 9027, "program_id": 10121, "run_id": 10021, "pipeline_ids": [19776, 19777]}
    signed = page_holds.sign(holds, "tok", 1)
    assert page_holds._unsign(signed, "tok", 1).workflow["run_id"] == 10021
