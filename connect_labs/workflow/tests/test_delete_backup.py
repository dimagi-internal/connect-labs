"""Deleting a workflow first writes a restorable backup (definition + render
code) to the labs DB, fail-closed. Runs are not backed up."""

from unittest import mock

import pytest

from connect_labs.labs.models import DeletedWorkflowBackup


def _wda():
    from connect_labs.workflow.data_access import WorkflowDataAccess

    return WorkflowDataAccess(access_token="tok", opportunity_id=1973)


def _definition(**overrides):
    """A stand-in WorkflowDefinitionRecord with the attrs _backup_definition reads.

    ``name`` is set after construction because ``mock.Mock(name=...)`` is
    reserved (it names the mock, not a ``.name`` attribute).
    """
    name = overrides.pop("name", "CHC PRE-RCT — Weekly Audit")
    defaults = dict(
        id=4644,
        opportunity_id=1973,
        template_type="weekly_dual_track_audit",
        data={"name": name, "statuses": [{"id": "pending"}], "config": {}},
    )
    defaults.update(overrides)
    m = mock.Mock(**defaults)
    m.name = name
    return m


def _render(code="<Workflow>hi</Workflow>"):
    return mock.Mock(id=4645, component_code=code)


@pytest.mark.django_db
def test_delete_definition_writes_backup_then_deletes():
    wda = _wda()
    wda.user = mock.Mock(username="jjackson")
    definition = _definition()

    with (
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "get_render_code", return_value=_render()),
        mock.patch.object(wda, "get_chat_history", return_value=None),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        wda.delete_definition(4644)

    backup = DeletedWorkflowBackup.objects.get(definition_id=4644)
    assert backup.opportunity_id == 1973
    assert backup.name == "CHC PRE-RCT — Weekly Audit"
    assert backup.template_type == "weekly_dual_track_audit"
    assert backup.definition_data == definition.data
    assert backup.render_code == "<Workflow>hi</Workflow>"
    assert backup.deleted_by == "jjackson"
    # The delete still ran, and the definition id was in the batch.
    assert 4644 in labs_api.delete_records.call_args[0][0]


@pytest.mark.django_db
def test_backup_stores_empty_render_when_no_render_code():
    wda = _wda()
    definition = _definition()

    with (
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "get_render_code", return_value=None),
        mock.patch.object(wda, "get_chat_history", return_value=None),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        wda.delete_definition(4644)

    backup = DeletedWorkflowBackup.objects.get(definition_id=4644)
    assert backup.render_code == ""
    assert backup.deleted_by == ""  # no user on this wda
    assert labs_api.delete_records.called


@pytest.mark.django_db
def test_fail_closed_aborts_delete_when_backup_write_fails():
    wda = _wda()
    definition = _definition()

    with (
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "get_render_code", return_value=_render()),
        mock.patch.object(wda, "get_chat_history", return_value=None),
        mock.patch.object(wda, "labs_api") as labs_api,
        mock.patch(
            "connect_labs.labs.models.DeletedWorkflowBackup.objects.create",
            side_effect=RuntimeError("db down"),
        ),
    ):
        with pytest.raises(RuntimeError):
            wda.delete_definition(4644)

    # Fail-closed: nothing was deleted.
    labs_api.delete_records.assert_not_called()
    assert DeletedWorkflowBackup.objects.count() == 0


@pytest.mark.django_db
def test_missing_definition_is_noop_backup_but_still_deletes():
    """A definition already gone leaves nothing to back up; delete proceeds."""
    wda = _wda()

    with (
        mock.patch.object(wda, "get_definition", return_value=None),
        mock.patch.object(wda, "get_render_code", return_value=None),
        mock.patch.object(wda, "get_chat_history", return_value=None),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        wda.delete_definition(4644)

    assert DeletedWorkflowBackup.objects.count() == 0
    assert 4644 in labs_api.delete_records.call_args[0][0]


@pytest.mark.django_db
def test_delete_linked_backs_up_only_the_definition_not_runs():
    wda = _wda()
    definition = _definition(data={"name": "X", "opportunity_ids": [1973]})

    with (
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "get_render_code", return_value=_render()),
        mock.patch.object(wda, "get_chat_history", return_value=None),
        mock.patch.object(wda, "list_runs", return_value=[]),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        wda.delete_definition(4644, delete_linked=True)

    # Exactly one backup row, holding the definition — no run payload.
    backup = DeletedWorkflowBackup.objects.get(definition_id=4644)
    assert backup.definition_data == {"name": "X", "opportunity_ids": [1973]}
    assert labs_api.delete_records.called
