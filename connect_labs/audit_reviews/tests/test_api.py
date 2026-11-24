"""Who may read and write audit reviews through the API."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from connect_labs.audit_reviews import api_views, service

pytestmark = pytest.mark.django_db

WORKFLOW = 900
SOURCE_WF = 800


def _definition(**config):
    cfg = {"audit_source_workflow_id": SOURCE_WF, "read_workflows": [SOURCE_WF]}
    cfg.update(config)
    return SimpleNamespace(id=WORKFLOW, data={"config": cfg})


@pytest.fixture
def dao(monkeypatch):
    fake = MagicMock()
    fake.get_definition.return_value = _definition()
    fake.list_runs.return_value = []
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: fake)
    monkeypatch.setattr(api_views, "_scope", lambda request: {"opportunity_id": 10301})
    return fake


def _user(staff=True):
    return SimpleNamespace(
        is_authenticated=True, is_staff=staff, email="someone@example.org", name="Staff One", pk=None
    )


def _get(user, **headers):
    request = RequestFactory().get(f"/labs/audit-reviews/api/{WORKFLOW}/world/?opportunity_id=10301", **headers)
    request.user = user
    return api_views.world_api(request, WORKFLOW)


def test_anonymous_gets_a_bare_401(dao):
    assert _get(AnonymousUser()).status_code == 401


def test_only_dimagi_staff(dao):
    assert _get(_user(staff=False)).status_code == 403


def test_a_sandboxed_call_reads_only_its_own_workflow(dao):
    assert _get(_user(), HTTP_X_LABS_SANDBOX_FOR=str(WORKFLOW + 1)).status_code == 403


def test_a_workflow_the_viewer_cannot_read_is_not_found(dao):
    dao.get_definition.return_value = None
    assert _get(_user()).status_code == 404


def test_the_followed_report_must_be_declared(dao):
    dao.get_definition.return_value = _definition(read_workflows=[])
    assert _get(_user()).status_code == 403


def test_staff_read_the_world(dao):
    response = _get(_user(), HTTP_X_LABS_SANDBOX_FOR=str(WORKFLOW))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["source"]["source_workflow_id"] == SOURCE_WF
    dao.close.assert_called()


def test_a_refused_decision_is_a_409_with_the_reason(dao, monkeypatch):
    def refuse(*a, **k):
        raise service.Refused("Closed – other needs a note.")

    monkeypatch.setattr(service, "record_decisions", refuse)
    request = RequestFactory().post(
        f"/labs/audit-reviews/api/{WORKFLOW}/decisions/",
        data=json.dumps({"decisions": [{"kind": "close", "entity": "program", "status": "closed_other"}]}),
        content_type="application/json",
    )
    request.user = _user()
    response = api_views.decisions_api(request, WORKFLOW)
    assert response.status_code == 409
    assert json.loads(response.content)["error"] == "Closed – other needs a note."


def test_a_failure_says_which_step_failed_and_how(dao, monkeypatch):
    def broken(source):
        raise TypeError("'>' not supported")

    monkeypatch.setattr(service, "world", broken)
    response = _get(_user())
    assert response.status_code == 500
    error = json.loads(response.content)["error"]
    assert "working out the reviews" in error and "TypeError" in error
    assert "not supported" not in error  # the type, not the message: messages can carry data


def test_opportunity_names_come_from_the_viewers_session_only():
    request = RequestFactory().get("/")
    request.session = {
        "labs_oauth": {
            "organization_data": {
                "opportunities": [{"id": 1, "name": "KMC Uganda"}, {"id": 2, "name": "Not in these reviews"}]
            }
        }
    }
    payload = {"D": {"source": {"opportunities": [{"id": 1}, {"id": 3}]}}}
    assert api_views._opportunity_names(request, payload) == {"1": "KMC Uganda"}
