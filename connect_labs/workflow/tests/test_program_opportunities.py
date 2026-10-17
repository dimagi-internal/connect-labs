"""A workflow that reads its program's opportunities, as the viewer sees them."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from connect_labs.workflow.program_opportunities import program_opportunity_ids, resolve, resolve_all, source_problem

TREE = {
    "opportunities": [
        {"id": 2154, "program": 217},
        {"id": 2304, "program": 217},
        {"id": 2356, "program": 217},
        {"id": 1976, "program": 176},
        {"id": 9, "program": None},
    ]
}


def _record(data):
    return SimpleNamespace(id=1, data=data)


def test_lists_the_programs_opportunities_ascending():
    assert program_opportunity_ids(TREE, 217) == [2154, 2304, 2356]


def test_exclusions_come_out():
    assert program_opportunity_ids(TREE, "217", exclude=[2304]) == [2154, 2356]


def test_source_problems():
    assert source_problem(None) is None
    assert source_problem({"program_id": 217}) is None
    assert source_problem({"program_id": 217, "exclude_opportunity_ids": [1]}) is None
    assert source_problem({"program_id": "217"})
    assert source_problem({"program_id": True})
    assert source_problem({"program": 217})
    assert source_problem({"program_id": 217, "exclude_opportunity_ids": ["x"]})
    assert source_problem([217])


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=TREE)
def test_resolves_from_the_viewers_tree_without_a_request(fetch):
    record = resolve(_record({"opportunity_source": {"program_id": 217}, "opportunity_ids": [2154]}), None, "tok")
    assert record.data["opportunity_ids"] == [2154, 2304, 2356]
    fetch.assert_called_once_with("tok")


def test_the_session_tree_is_used_on_the_web():
    request = SimpleNamespace(session={"labs_oauth": {}})
    with (
        patch("connect_labs.labs.context.get_org_data", return_value=TREE),
        patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data") as fetch,
    ):
        record = resolve(_record({"opportunity_source": {"program_id": 217}}), request, "tok")
    assert record.data["opportunity_ids"] == [2154, 2304, 2356]
    fetch.assert_not_called()


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=None)
def test_no_tree_keeps_the_stored_list(_fetch):
    record = resolve(_record({"opportunity_source": {"program_id": 217}, "opportunity_ids": [2154]}), None, "tok")
    assert record.data["opportunity_ids"] == [2154]


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=TREE)
def test_a_program_the_viewer_sees_nothing_of_keeps_the_stored_list(_fetch):
    record = resolve(_record({"opportunity_source": {"program_id": 999}, "opportunity_ids": [2154]}), None, "tok")
    assert record.data["opportunity_ids"] == [2154]


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data")
def test_a_workflow_without_a_source_never_reads_the_tree(fetch):
    records = [_record({"opportunity_ids": [1]}), _record({}), None]
    resolve_all(records, None, "tok")
    resolve(None, None, "tok")
    fetch.assert_not_called()
    assert records[0].data == {"opportunity_ids": [1]}


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=TREE)
def test_the_tree_is_read_once_for_a_list(fetch):
    records = [
        _record({"opportunity_source": {"program_id": 217}}),
        _record({"opportunity_source": {"program_id": 176}}),
    ]
    resolve_all(records, None, "tok")
    assert [r.data["opportunity_ids"] for r in records] == [[2154, 2304, 2356], [1976]]
    assert fetch.call_count == 1


@patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=TREE)
def test_get_definition_resolves(_fetch):
    with (
        patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as api_cls,
        patch("connect_labs.workflow.data_access.settings") as settings,
    ):
        settings.CONNECT_PRODUCTION_URL = "https://example.com"
        api = MagicMock()
        api_cls.return_value = api
        from connect_labs.workflow.data_access import WorkflowDataAccess, WorkflowDefinitionRecord

        api.get_record_by_id.return_value = WorkflowDefinitionRecord(
            {
                "id": 5,
                "experiment": "workflow",
                "type": "workflow_definition",
                "data": {"name": "R", "opportunity_source": {"program_id": 217}, "opportunity_ids": [2154]},
                "opportunity_id": None,
                "program_id": 217,
            }
        )
        wda = WorkflowDataAccess(program_id=217, access_token="tok")
        definition = wda.get_definition(5)
    assert definition.opportunity_ids == [2154, 2304, 2356]
