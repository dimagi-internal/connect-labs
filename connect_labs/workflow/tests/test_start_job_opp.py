"""start_job_api must run the job under the run's OWNING opportunity, not the
caller's (possibly drifted) session opp — else the job's get_run 404s and dies.
"""

import json
from unittest import mock

from django.test import RequestFactory


def _req(rf, body, session_opp):
    req = rf.post("/labs/workflow/api/run/4864/job/start/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {"opportunity_id": session_opp}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, is_superuser=True, username="jj")
    return req


def test_start_job_uses_runs_owning_opp_not_session_opp():
    from connect_labs.workflow import views as m

    rf = RequestFactory()
    # session opp has drifted to 1978, but the run is owned by 1973 (which the
    # render reports in job_config.opportunity_id).
    body = {"job_config": {"job_type": "weekly_dual_track_audit_create", "opportunity_id": 1973}}
    req = _req(rf, body, session_opp=1978)

    owning_run = mock.Mock(opportunity_id=1973)

    def wda_factory(*args, **kwargs):
        inst = mock.Mock()
        # get_run only finds the run when the client is scoped to its owning opp.
        inst.get_run.return_value = owning_run if kwargs.get("opportunity_id") == 1973 else None
        return inst

    with (
        mock.patch.object(m, "WorkflowDataAccess", side_effect=wda_factory),
        mock.patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        job.delay.return_value = mock.Mock(id="task-1")
        resp = m.start_job_api(req, 4864)

    assert resp.status_code == 200
    # The job was dispatched with the run's OWNING opp (1973), not the session opp (1978).
    assert job.delay.call_args.kwargs["opportunity_id"] == 1973


def _prog_req(rf, body, program_id):
    """Program-scoped request: labs_context carries program_id, NO opportunity_id."""
    req = rf.post("/labs/workflow/api/run/5112/job/start/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {"program_id": program_id}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, is_superuser=True, username="jj")
    return req


def test_start_job_program_dispatch_when_program_scoped():
    """A program-owned workflow (program_id in context, no owning opp) dispatches
    the job with program_id and NO opportunity_id — no 'opportunity_id required'."""
    from connect_labs.workflow import views as m

    rf = RequestFactory()
    # program-owned run: instance.opportunity_id is null, so job_config carries none.
    body = {"job_config": {"job_type": "program_audit_generate", "run_id": 5112, "opportunity_id": None}}
    req = _prog_req(rf, body, program_id=176)

    with (
        mock.patch.object(m, "WorkflowDataAccess") as wda,
        mock.patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        wda.return_value.get_run.return_value = mock.Mock(id=5112)
        job.delay.return_value = mock.Mock(id="task-1")
        resp = m.start_job_api(req, 5112)

    assert resp.status_code == 200
    kwargs = job.delay.call_args.kwargs
    assert kwargs["program_id"] == 176
    assert kwargs.get("opportunity_id") is None


def test_start_job_trusts_job_config_program_scope_over_stale_session_opportunity_id():
    """Reproduces the live "Create Audits" 500 on a program-owned Weekly
    Dual-Track Audit run: an unrelated same-page background fetch (the
    per-opportunity sessions-list call, whose URL carries a raw
    opportunity_id query param) clobbers the session's labs_context to
    {"opportunity_id": <some member opp>} with NO program_id, wiping out the
    program scope the page itself was in. When "Create Audits" is then
    clicked, job_config still correctly declares program_id with no
    opportunity_id (WorkflowRunView builds it from the run record's own
    ownership) — that explicit signal must win over the stale session opp,
    or dispatch wrongly goes opp-scoped and 404s on get_run()."""
    from connect_labs.workflow import views as m

    rf = RequestFactory()
    body = {
        "job_config": {
            "job_type": "weekly_dual_track_audit_create",
            "run_id": 6823,
            "opportunity_id": None,
            "program_id": 176,
        }
    }
    req = rf.post("/labs/workflow/api/run/6823/job/start/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "tok"}}
    # Corrupted session context: a stale member-opp id, program_id absent.
    req.labs_context = {"opportunity_id": 1976}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, is_superuser=True, username="jj")

    with (
        mock.patch.object(m, "WorkflowDataAccess") as wda,
        mock.patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        wda.return_value.get_run.return_value = mock.Mock(id=6823)
        job.delay.return_value = mock.Mock(id="task-1")
        resp = m.start_job_api(req, 6823)

    assert resp.status_code == 200
    kwargs = job.delay.call_args.kwargs
    assert kwargs["program_id"] == 176
    assert kwargs.get("opportunity_id") is None


def test_start_job_reads_program_id_from_job_config():
    """program_id may also arrive on job_config (render payload) rather than context."""
    from connect_labs.workflow import views as m

    rf = RequestFactory()
    body = {"job_config": {"job_type": "program_audit_generate", "run_id": 5112, "program_id": 176}}
    req = rf.post("/labs/workflow/api/run/5112/job/start/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {}  # neither opp nor program in session context
    req.user = mock.Mock(is_authenticated=True, is_staff=True, is_superuser=True, username="jj")

    with (
        mock.patch.object(m, "WorkflowDataAccess") as wda,
        mock.patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        wda.return_value.get_run.return_value = mock.Mock(id=5112)
        job.delay.return_value = mock.Mock(id="task-1")
        resp = m.start_job_api(req, 5112)

    assert resp.status_code == 200
    assert job.delay.call_args.kwargs["program_id"] == 176


def test_start_job_still_errors_when_no_opp_and_no_program():
    """With neither an opp candidate nor a program_id, the old 400 still applies."""
    from connect_labs.workflow import views as m

    rf = RequestFactory()
    body = {"job_config": {"job_type": "x"}}
    req = rf.post("/labs/workflow/api/run/1/job/start/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, is_superuser=True, username="jj")

    resp = m.start_job_api(req, 1)
    assert resp.status_code == 400
