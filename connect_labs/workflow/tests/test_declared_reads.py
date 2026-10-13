"""A sandboxed workflow reads other workflows' runs only when it declares them.

Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Cross-workflow reads".
THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.workflow import declared_reads
from connect_labs.workflow.data_access import WorkflowRunRecord
from connect_labs.workflow.tests.test_page_mode import _definition

READER = 47
DECLARED = 31
STRANGER = 77

COMPANION = {
    "name": "Worker review",
    "config": {"source_workflow_id": DECLARED},
    "workflow_sources": [{"alias": "report", "workflow": 32, "read": "latest_run"}],
}


def _run(definition_id, run_id=900, state=None, created_at=""):
    return WorkflowRunRecord(
        {"id": run_id, "experiment": "w", "type": "workflow_run", "opportunity_id": 10, "program_id": None,
         "data": {"status": "completed", "definition_id": definition_id, "state": state or {}, "snapshot": {"s": 1},
                  "created_at": created_at}}
    )  # fmt: skip


def _req(path, sandbox_for=READER, method="get", body=None):
    from django.contrib.auth import get_user_model

    headers = {} if sandbox_for is None else {"HTTP_X_LABS_SANDBOX_FOR": str(sandbox_for)}
    factory = RequestFactory()
    if method == "post":
        req = factory.post(path, data=json.dumps(body or {}), content_type="application/json", **headers)
    else:
        req = factory.get(path, **headers)
    req.user, _ = get_user_model().objects.get_or_create(username="amina")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.labs_context = {"opportunity_id": 10}
    return req


@pytest.fixture
def wda():
    """Both the view's data access and the reader lookup's."""
    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as V,
        patch("connect_labs.workflow.data_access.WorkflowDataAccess") as D,
    ):
        D.return_value.get_definition.return_value = _definition(COMPANION, id_=READER)
        yield V.return_value


# --- what a definition declares ----------------------------------------------------


def test_every_kind_of_declaration_is_readable():
    data = {
        "config": {
            "source_workflow_id": 1,
            "source_definition_id": "2",
            "report_definition_id": 3,
            "watched_sources": [{"opportunity_id": 10, "workflow_definition_id": 4}],
            "per_opp_instances": [{"opportunity_id": 11, "workflow_definition_id": 5}],
            "watched_source": {"creator_definition_id": 6, "opportunity_ids": [10]},
            "read_workflows": [7, {"workflow": 8}],
        },
        "workflow_sources": [{"alias": "a", "workflow": 9}],
    }
    assert declared_reads.readable_definition_ids(_definition(data, id_=47)) == {47, 1, 2, 3, 4, 5, 6, 7, 8, 9}


def test_nothing_declared_reads_only_itself():
    assert declared_reads.readable_definition_ids(_definition({"config": {}}, id_=47)) == {47}


# --- on the server ------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("definition_id, status", [(DECLARED, 200), (READER, 200), (STRANGER, 403)])
def test_a_run_is_read_only_if_its_workflow_is_declared(wda, definition_id, status):
    from connect_labs.workflow.views import get_run_api

    wda.get_run.return_value = _run(definition_id)
    response = get_run_api(_req("/labs/workflow/api/run/900/"), run_id=900)
    assert response.status_code == status
    if status == 403:
        assert json.loads(response.content)["code"] == "undeclared_workflow"
        assert "snapshot" not in response.content.decode()


@pytest.mark.django_db
def test_a_request_that_is_not_the_frames_is_not_restricted(wda):
    from connect_labs.workflow.views import get_run_api

    wda.get_run.return_value = _run(STRANGER)
    assert get_run_api(_req("/labs/workflow/api/run/900/", sandbox_for=None), run_id=900).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["none", "abc", "0"])
def test_a_frame_naming_no_definition_reads_no_run(wda, header):
    from connect_labs.workflow.views import get_run_api

    wda.get_run.return_value = _run(READER)
    assert get_run_api(_req("/labs/workflow/api/run/900/", sandbox_for=header), run_id=900).status_code == 403


@pytest.mark.django_db
def test_an_unreadable_reader_declares_nothing(wda):
    from connect_labs.workflow.views import get_run_api

    with patch("connect_labs.workflow.data_access.WorkflowDataAccess") as D:
        D.return_value.get_definition.return_value = None
        wda.get_run.return_value = _run(DECLARED)
        assert get_run_api(_req("/labs/workflow/api/run/900/"), run_id=900).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", ["get_snapshot_api", "preview_snapshot_api"])
def test_a_snapshot_of_an_undeclared_run_is_refused(wda, view_name):
    from connect_labs.workflow import views

    wda.get_run.return_value = _run(STRANGER)
    response = getattr(views, view_name)(_req("/labs/workflow/api/run/900/snapshot/"), run_id=900)
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_name, path",
    [
        ("run_history_api", "/labs/workflow/api/77/runs/history/?keys=snapshot.meta"),
        ("semantic_explain_api", "/labs/workflow/api/77/indicator-definitions/"),
        ("semantic_indicators_api", "/labs/workflow/api/77/semantic/"),
    ],
)
def test_another_workflows_history_and_indicators_need_declaring(wda, view_name, path):
    from connect_labs.workflow import views

    response = getattr(views, view_name)(_req(path), definition_id=STRANGER)
    assert response.status_code == 403
    wda.list_runs.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_name", ["flw_audit_report_history_api", "flw_daily_indicator_history_api", "flw_daily_summary_history_api"]
)
def test_history_named_by_query_needs_declaring(wda, view_name):
    from connect_labs.workflow import views

    response = getattr(views, view_name)(_req(f"/x/?definition_id={STRANGER}"))
    assert response.status_code == 403
    wda.list_runs.assert_not_called()


@pytest.mark.django_db
def test_a_declared_history_is_read(wda):
    from connect_labs.workflow.views import run_history_api

    wda.list_runs.return_value = []
    response = run_history_api(_req("/x/?keys=snapshot.meta"), definition_id=DECLARED)
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", ["prev_categories_api", "open_run_state_api", "run_category_history_api"])
def test_an_opportunity_wide_listing_shows_only_declared_workflows_runs(wda, view_name):
    from connect_labs.workflow import views

    mine = _run(DECLARED, run_id=1, state={"worker_results": {"w1": {"result": "suspended"}}}, created_at="2026-10-01")
    # Newer, so the "most recent run" readers would pick it if it were shown.
    theirs = _run(
        STRANGER, run_id=2, state={"worker_results": {"w2": {"result": "suspended"}}}, created_at="2026-10-02"
    )
    wda.list_runs.return_value = [mine, theirs]
    body = getattr(views, view_name)(_req("/x/")).content.decode()
    assert "w1" in body or '"id": 1' in body
    assert "w2" not in body and '"id": 2' not in body
    # Without the header (labs' own page code), both.
    wda.list_runs.return_value = [mine, theirs]
    plain = getattr(views, view_name)(_req("/x/", sandbox_for=None)).content.decode()
    assert "w2" in plain or '"id": 2' in plain


@pytest.mark.django_db
@pytest.mark.parametrize(
    "watched, status",
    [
        ([{"opportunity_id": 10, "workflow_definition_id": DECLARED}], None),
        # Run state is frame-writable, so an opportunity the workflow covers is not a
        # declaration (review finding 3): undeclared is refused wherever it sits.
        ([{"opportunity_id": 10, "workflow_definition_id": STRANGER}], 403),
        ([{"opportunity_id": 99, "workflow_definition_id": STRANGER}], 403),
    ],
)
def test_a_rollup_from_the_frame_watches_only_what_is_declared_or_covered(wda, watched, status):
    from connect_labs.workflow.views import start_job_api

    reader = _definition({**COMPANION, "opportunity_ids": [10]}, id_=READER)
    with (
        patch("connect_labs.workflow.data_access.WorkflowDataAccess") as D,
        patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        D.return_value.get_definition.return_value = reader
        job.delay.return_value = MagicMock(id="task-1")
        wda.get_run.return_value = _run(READER, state={"watched_sources": watched})
        request = _req(
            "/labs/workflow/api/run/900/job/start/?opportunity_id=10",
            method="post",
            body={"job_config": {"job_type": "program_admin_rollup", "run_id": 900}},
        )
        response = start_job_api(request, run_id=900)
    if status is None:
        assert response.status_code != 403, response.content
        job.delay.assert_called_once()
    else:
        assert response.status_code == status
        job.delay.assert_not_called()


@pytest.mark.django_db
def test_a_rollup_of_another_workflows_run_is_refused(wda):
    from connect_labs.workflow.views import start_job_api

    with patch("connect_labs.workflow.tasks.run_workflow_job") as job:
        wda.get_run.return_value = _run(DECLARED, state={})
        request = _req(
            "/labs/workflow/api/run/900/job/start/?opportunity_id=10",
            method="post",
            body={"job_config": {"job_type": "audit_par_rollup", "run_id": 900}},
        )
        assert start_job_api(request, run_id=900).status_code == 403
        job.delay.assert_not_called()


# --- the host is told what it may name -----------------------------------------------


@pytest.mark.django_db
def test_the_sandbox_payload_names_the_declared_workflows():
    from connect_labs.workflow.tests.test_workflow_sandbox import _render_run

    body = _render_run({**COMPANION, "config": {**COMPANION["config"], "sandbox": True}}, "?program_id=25&run_id=503")
    assert '"readable_definitions": [31, 32, 47]' in body.content.decode()


# --- the adversarial review's findings, each pinned ---------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("sandbox_for", [READER, None])
def test_a_job_acts_only_on_the_run_it_is_started_on(wda, sandbox_for):
    """Finding 1: handlers act on job_config.run_id, so naming another run there
    pointed a job (and its writes) at it. Refused for everyone: no template does it."""
    from connect_labs.workflow.views import start_job_api

    with patch("connect_labs.workflow.tasks.run_workflow_job") as job:
        wda.get_run.return_value = _run(READER, state={})
        request = _req(
            "/labs/workflow/api/run/900/job/start/?opportunity_id=10",
            sandbox_for=sandbox_for,
            method="post",
            body={"job_config": {"job_type": "muac_plausibility_compute", "run_id": 901}},
        )
        response = start_job_api(request, run_id=900)
    assert response.status_code == 400
    job.delay.assert_not_called()


@pytest.mark.django_db
def test_a_job_with_no_run_named_is_given_its_own():
    from connect_labs.workflow.views import start_job_api

    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as V,
        patch("connect_labs.workflow.tasks.run_workflow_job") as job,
    ):
        V.return_value.get_run.return_value = _run(READER, state={})
        job.delay.return_value = MagicMock(id="task-1")
        request = _req(
            "/labs/workflow/api/run/900/job/start/?opportunity_id=10",
            sandbox_for=None,
            method="post",
            body={"job_config": {"job_type": "muac_plausibility_compute"}},
        )
        start_job_api(request, run_id=900)
    assert job.delay.call_args.kwargs["job_config"]["run_id"] == 900


@pytest.mark.django_db
def test_a_jobs_status_of_an_undeclared_run_is_not_read(wda):
    """Finding 2: the status endpoint read any run's active job."""
    from connect_labs.workflow.views import read_active_job

    wda.get_run.return_value = _run(STRANGER, state={"active_job": {"job_id": "j", "status": "running"}})
    assert read_active_job(_req("/x/?run_id=900"), 900) is None
    assert read_active_job(_req("/x/?run_id=900", sandbox_for=None), 900) == {"job_id": "j", "status": "running"}


@pytest.mark.django_db
def test_the_reader_is_read_where_it_lives_when_the_session_has_moved(wda):
    """Finding 4: a program-owned definition is invisible at an opportunity's scope;
    the host stamps where it lives."""
    from connect_labs.workflow.views import get_run_api

    def definition_in(**scope):
        access = MagicMock()
        access.get_definition.return_value = (
            _definition(COMPANION, id_=READER) if scope.get("program_id") == 25 else None
        )
        return access

    with patch(
        "connect_labs.workflow.data_access.WorkflowDataAccess", side_effect=lambda request, **kw: definition_in(**kw)
    ):
        wda.get_run.return_value = _run(DECLARED)
        request = _req("/labs/workflow/api/run/900/")
        request.META["HTTP_X_LABS_SANDBOX_SCOPE"] = "program_id=25"
        assert get_run_api(request, run_id=900).status_code == 200
        unstamped = _req("/labs/workflow/api/run/900/")
        assert get_run_api(unstamped, run_id=900).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["flw_review", "worker_review"])
def test_a_report_that_names_this_review_lets_it_read_its_run(wda, key):
    """Finding 5: the opportunity report links its worker rows to the review with
    ?source_run=<its own run>; naming the review is the report's consent."""
    from connect_labs.workflow.views import preview_snapshot_api

    report = _definition({"name": "Opp report", "config": {key: {"workflow_id": READER, "run_id": 5}}}, id_=STRANGER)
    other = _definition({"name": "Opp report", "config": {key: {"workflow_id": 12345, "run_id": 5}}}, id_=STRANGER)
    for target, status in ((report, 200), (other, 403)):
        with patch("connect_labs.workflow.data_access.WorkflowDataAccess") as D:
            D.return_value.get_definition.side_effect = lambda i, t=target: (
                _definition(COMPANION, id_=READER) if i == READER else t
            )
            wda.get_run.return_value = _run(STRANGER)
            response = preview_snapshot_api(_req("/labs/workflow/api/run/900/snapshot/preview/"), run_id=900)
        assert response.status_code == status, target.data
