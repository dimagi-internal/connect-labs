"""Multi-opp delete cascade: a run/definition's audit sessions are gathered
across every opportunity it spans, not just the primary one (the labs API
scopes each GET to one opp). Regression test for orphaned audits on delete of
a multi-opp workflow run."""

from unittest import mock


def _wda():
    from connect_labs.workflow.data_access import WorkflowDataAccess

    return WorkflowDataAccess(access_token="tok", opportunity_id=1973)


def _ada_factory_20_per_opp():
    """An AuditDataAccess stand-in: each opp returns 20 distinct sessions."""

    def factory(*args, **kwargs):
        opp = kwargs["opportunity_id"]
        inst = mock.Mock()
        inst.get_sessions_by_workflow_run.return_value = [mock.Mock(id=opp * 100 + i) for i in range(20)]
        inst.close = mock.Mock()
        return inst

    return factory


def test_delete_run_gathers_audits_across_all_opps():
    wda = _wda()
    run = mock.Mock(definition_id=4683, opportunity_id=1973)
    definition = mock.Mock(data={"opportunity_ids": [1973, 1976, 1978, 1982]})

    with (
        mock.patch("connect_labs.audit.data_access.AuditDataAccess", side_effect=_ada_factory_20_per_opp()) as ADA,
        mock.patch.object(wda, "get_run", return_value=run),
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        result = wda.delete_run(4698, delete_linked=True)

    # One scoped AuditDataAccess per opp; all four opps queried.
    assert ADA.call_count == 4
    assert {c.kwargs["opportunity_id"] for c in ADA.call_args_list} == {1973, 1976, 1978, 1982}
    # 80 audit sessions (4 opps x 20) + the run itself deleted in one batch.
    assert result["audit_sessions"] == 80
    assert result["run"] == 1
    deleted_ids = labs_api.delete_records.call_args[0][0]
    assert len(deleted_ids) == 81
    assert 4698 in deleted_ids


def test_delete_run_dedupes_session_ids_seen_in_multiple_opps():
    """If the same session id were returned under two opps, it's deleted once."""
    wda = _wda()
    run = mock.Mock(definition_id=4683, opportunity_id=1973)
    definition = mock.Mock(data={"opportunity_ids": [1973, 1976]})

    def factory(*args, **kwargs):
        inst = mock.Mock()
        inst.get_sessions_by_workflow_run.return_value = [mock.Mock(id=7), mock.Mock(id=8)]  # same ids each opp
        inst.close = mock.Mock()
        return inst

    with (
        mock.patch("connect_labs.audit.data_access.AuditDataAccess", side_effect=factory),
        mock.patch.object(wda, "get_run", return_value=run),
        mock.patch.object(wda, "get_definition", return_value=definition),
        mock.patch.object(wda, "labs_api") as labs_api,
    ):
        result = wda.delete_run(4698, delete_linked=True)

    assert result["audit_sessions"] == 2  # deduped, not 4
    deleted_ids = labs_api.delete_records.call_args[0][0]
    assert sorted(deleted_ids) == [7, 8, 4698]
