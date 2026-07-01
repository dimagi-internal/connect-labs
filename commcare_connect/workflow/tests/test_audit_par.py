from unittest import mock

from commcare_connect.workflow.templates.audit_par import summarize_run_sessions


class FakeSession:
    def __init__(self, opp, tag, flw, stats, name=None, status="completed", sid=None, img=0):
        self.opportunity_id = opp
        self.tag = tag
        self.flw_username = flw
        self.flw_display_name = name
        self.status = status
        self.id = sid
        self.image_count = img
        self._stats = stats

    def get_assessment_stats(self):
        return self._stats


def test_groups_by_tag_and_builds_flw_rows():
    sessions = [
        FakeSession(
            101, "muac", "flw1", {"pass": 8, "fail": 2, "pending": 0, "ai_no_match": 2}, name="Ana", sid=11, img=10
        ),
        FakeSession(
            101, "rest", "flw1", {"pass": 5, "fail": 0, "pending": 5, "ai_no_match": 0}, name="Ana", sid=12, img=8
        ),
        FakeSession(101, "muac", "flw2", {"pass": 4, "fail": 0, "pending": 0, "ai_no_match": 0}, sid=13, img=6),
        FakeSession(999, "muac", "flwX", {"pass": 1, "fail": 0, "pending": 0, "ai_no_match": 0}, sid=99),  # other opp
    ]
    out = summarize_run_sessions(sessions, opportunity_id=101)

    assert out["by_tag"]["muac"]["sessions"] == 2
    assert out["by_tag"]["muac"]["pass"] == 12
    assert out["by_tag"]["muac"]["ai_flagged"] == 2
    assert out["by_tag"]["rest"]["pending"] == 5

    # image_count (Global Constraint): images come from the session's image_count,
    # never assessment totals.
    assert out["by_tag"]["muac"]["images"] == 16  # 10 + 6
    assert out["by_tag"]["rest"]["images"] == 8

    rows = {r["flw_id"]: r for r in out["flw_rows"]}
    assert rows["flw1"]["flw_name"] == "Ana"
    assert rows["flw1"]["muac"]["fail"] == 2
    assert rows["flw1"]["muac"]["images"] == 10
    assert rows["flw1"]["rest"]["pending"] == 5
    assert rows["flw2"]["rest"] is None
    assert "flwX" not in rows  # filtered to opp 101

    # session_id deviation: each cell carries the audit session id for deep-linking
    assert rows["flw1"]["muac"]["session_id"] == 11
    assert rows["flw1"]["rest"]["session_id"] == 12
    assert rows["flw2"]["muac"]["session_id"] == 13


def _run(run_id, ws, we, completed=False):
    r = mock.Mock()
    r.id = run_id
    r.data = {"state": {"window_start": ws, "window_end": we}}
    r.completed_at = we
    r.is_completed = completed
    return r


def test_rollup_buckets_runs_per_opp_and_week():
    from commcare_connect.workflow.templates import audit_par as m

    state = {
        "window_start": "2026-06-01",
        "window_end": "2026-06-30",
        "watched_source": {"creator_definition_id": 42, "opportunity_ids": [101, 102]},
    }
    runs = [_run(501, "2026-06-01", "2026-06-07"), _run(502, "2026-06-08", "2026-06-14")]

    sessions_by_run = {
        501: [
            FakeSession(101, "muac", "flw1", {"pass": 3, "fail": 1, "pending": 0, "ai_no_match": 1}),
            FakeSession(102, "rest", "flw9", {"pass": 2, "fail": 0, "pending": 2, "ai_no_match": 0}),
        ],
        502: [FakeSession(101, "muac", "flw1", {"pass": 4, "fail": 0, "pending": 0, "ai_no_match": 0})],
    }

    with mock.patch.object(m, "WorkflowDataAccess") as WDA, mock.patch.object(m, "AuditDataAccess") as ADA:
        WDA.return_value.list_runs.return_value = runs
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: sessions_by_run.get(rid, [])

        out = m.compute_audit_par_rollup(state=state, access_token="tok")

    # Load-bearing: a SEPARATE opp-scoped AuditDataAccess must be built per opp
    # (the labs API enforces opp scope per request; a shared DAO returns 0 for
    # non-primary opps). Assert the scoping, not just the bucketed output.
    assert ADA.call_count == 2
    scoped_opp_ids = {c.kwargs["opportunity_id"] for c in ADA.call_args_list}
    assert scoped_opp_ids == {101, 102}

    opp101 = next(s for s in out["watched_summary"] if s["opportunity_id"] == 101)
    assert len(opp101["weeks"]) == 2
    wk1 = opp101["weeks"][0]
    assert wk1["run_id"] == 501
    assert wk1["by_tag"]["muac"]["fail"] == 1
    opp102 = next(s for s in out["watched_summary"] if s["opportunity_id"] == 102)
    assert opp102["weeks"][0]["by_tag"]["rest"]["pending"] == 2


def test_rollup_missing_source_returns_error():
    from commcare_connect.workflow.templates import audit_par as m

    out = m.compute_audit_par_rollup(state={"watched_source": {}}, access_token="tok")
    assert out["error"] == "missing_source"


def test_rollup_without_window_includes_all_creator_runs():
    """No report window set → every one of the creator's runs is included."""
    from commcare_connect.workflow.templates import audit_par as m

    state = {"watched_source": {"creator_definition_id": 42, "opportunity_ids": [101]}}  # no window
    runs = [_run(501, "2026-06-01", "2026-06-07"), _run(502, "2026-08-20", "2026-08-26")]
    sessions_by_run = {
        501: [FakeSession(101, "muac", "flw1", {"pass": 1, "fail": 0, "pending": 0, "ai_no_match": 0})],
        502: [FakeSession(101, "rest", "flw2", {"pass": 0, "fail": 0, "pending": 2, "ai_no_match": 0})],
    }
    with (
        mock.patch.object(m, "WorkflowDataAccess") as WDA,
        mock.patch.object(m, "AuditDataAccess") as ADA,
    ):
        WDA.return_value.list_runs.return_value = runs
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: sessions_by_run.get(rid, [])
        out = m.compute_audit_par_rollup(state=state, access_token="tok")

    assert "error" not in out
    weeks = out["watched_summary"][0]["weeks"]
    assert {w["run_id"] for w in weeks} == {501, 502}  # both runs kept despite no window


def test_rollup_lists_runs_scoped_per_opp():
    """Regression: list_runs must be opp-scoped. An unscoped WorkflowDataAccess
    query returns only public records (workflow runs aren't public) → 0 runs →
    empty report. The rollup must instantiate the DAO with each watched opp."""
    from commcare_connect.workflow.templates import audit_par as m

    state = {"watched_source": {"creator_definition_id": 42, "opportunity_ids": [101, 202]}}
    with (
        mock.patch.object(m, "WorkflowDataAccess") as WDA,
        mock.patch.object(m, "AuditDataAccess") as ADA,
    ):
        WDA.return_value.list_runs.return_value = [_run(501, "2026-06-01", "2026-06-07")]
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: []
        m.compute_audit_par_rollup(state=state, access_token="tok")

    # WorkflowDataAccess was constructed once per watched opp, each with its opp id.
    opp_kwargs = {c.kwargs.get("opportunity_id") for c in WDA.call_args_list}
    assert opp_kwargs == {101, 202}
    assert all(c.kwargs.get("opportunity_id") is not None for c in WDA.call_args_list)


def test_rollup_reports_per_source_completion_status():
    """watched_sources model: each source lists its OWN opp's runs (filtered to
    that source's workflow_definition_id) and reports a per-week completion block.
    A completed run reports open_audits == 0."""
    from commcare_connect.workflow.templates import audit_par as m

    state = {"watched_sources": [{"opportunity_id": 1973, "workflow_definition_id": 42}]}
    run = _run(501, "2026-06-21", "2026-06-27", completed=True)
    sessions = [
        FakeSession(1973, "muac", "flw1", {"pass": 8, "fail": 2, "pending": 0, "ai_no_match": 1}, sid=1, img=10),
        FakeSession(1973, "rest", "flw1", {"pass": 5, "fail": 0, "pending": 0, "ai_no_match": 0}, sid=2, img=5),
    ]
    with mock.patch.object(m, "WorkflowDataAccess") as WDA, mock.patch.object(m, "AuditDataAccess") as ADA:
        WDA.return_value.list_runs.return_value = [run]
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: sessions
        out = m.compute_audit_par_rollup(state=state, access_token="tok")

    # list_runs is filtered to the source's workflow_definition_id, opp-scoped.
    WDA.return_value.list_runs.assert_called_once_with(42)
    assert WDA.call_args.kwargs["opportunity_id"] == 1973

    entry = out["watched_summary"][0]
    assert entry["opportunity_id"] == 1973
    assert entry["workflow_definition_id"] == 42
    wk = entry["weeks"][0]
    assert wk["definition_id"] == 42
    assert wk["completion"]["status"] == "completed"
    assert wk["completion"]["open_audits"] == 0
    assert wk["completion"]["total_audits"] == 2


def test_rollup_reports_open_audits_for_in_progress_run():
    from commcare_connect.workflow.templates import audit_par as m

    state = {"watched_sources": [{"opportunity_id": 1973, "workflow_definition_id": 42}]}
    run = _run(501, "2026-06-21", "2026-06-27", completed=False)
    sessions = [
        FakeSession(
            1973, "muac", "flw1", {"pass": 8, "fail": 0, "pending": 0, "ai_no_match": 0}, status="completed", sid=1
        ),
        FakeSession(
            1973, "rest", "flw2", {"pass": 0, "fail": 0, "pending": 3, "ai_no_match": 0}, status="in_progress", sid=2
        ),
    ]
    with mock.patch.object(m, "WorkflowDataAccess") as WDA, mock.patch.object(m, "AuditDataAccess") as ADA:
        WDA.return_value.list_runs.return_value = [run]
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: sessions
        out = m.compute_audit_par_rollup(state=state, access_token="tok")

    wk = out["watched_summary"][0]["weeks"][0]
    assert wk["completion"]["status"] == "in_progress"
    assert wk["completion"]["open_audits"] == 1
    assert wk["completion"]["total_audits"] == 2


def test_rollup_backward_compat_watched_source_singular_adapts_to_sources():
    """When only the legacy singular watched_source is present, it is adapted to a
    one-element-per-opp watched_sources list (same creator def for each opp)."""
    from commcare_connect.workflow.templates import audit_par as m

    state = {"watched_source": {"creator_definition_id": 42, "opportunity_ids": [101, 102]}}
    with mock.patch.object(m, "WorkflowDataAccess") as WDA, mock.patch.object(m, "AuditDataAccess") as ADA:
        WDA.return_value.list_runs.return_value = [_run(501, "2026-06-01", "2026-06-07")]
        ADA.return_value.get_sessions_by_workflow_run.side_effect = lambda rid: []
        out = m.compute_audit_par_rollup(state=state, access_token="tok")

    entries = {e["opportunity_id"]: e for e in out["watched_summary"]}
    assert set(entries) == {101, 102}
    assert entries[101]["workflow_definition_id"] == 42
    assert entries[102]["workflow_definition_id"] == 42
    # each source lists its own opp's runs, scoped to that opp
    opp_kwargs = {c.kwargs.get("opportunity_id") for c in WDA.call_args_list}
    assert opp_kwargs == {101, 102}


def test_audit_par_rollup_persists_into_run_state():
    from commcare_connect.workflow.job_handlers import audit_par as h

    run = mock.Mock()
    run.is_completed = False
    run.data = {
        "state": {
            "window_start": "2026-06-01",
            "window_end": "2026-06-30",
            "watched_source": {"creator_definition_id": 42, "opportunity_ids": [101]},
        }
    }

    with mock.patch.object(h, "WorkflowDataAccess") as WDA, mock.patch.object(h, "compute_audit_par_rollup") as comp:
        WDA.return_value.get_run.return_value = run
        comp.return_value = {
            "watched_summary": [{"opportunity_id": 101, "weeks": []}],
            "window_start": "2026-06-01",
            "window_end": "2026-06-30",
        }
        result = h.audit_par_rollup({"run_id": 700, "opportunity_id": 101}, access_token="tok")

    WDA.return_value.update_run_state.assert_called_once()
    assert result["successful"] == 1


def test_par_template_registered_saved_runs():
    from commcare_connect.workflow.templates import get_template

    tpl = get_template("audit_par")
    assert tpl["multi_opp"] is True
    assert tpl["supports_saved_runs"] is True
    assert "watched_summary" in tpl["snapshot_inputs"]["state_keys"]
    assert "audit_par_rollup" in tpl["render_code"]
