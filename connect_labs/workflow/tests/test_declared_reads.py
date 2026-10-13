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
        # An opportunity the workflow covers: its own per-opp instance, undeclared but in scope.
        ([{"opportunity_id": 10, "workflow_definition_id": STRANGER}], None),
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
