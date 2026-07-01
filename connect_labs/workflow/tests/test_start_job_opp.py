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
