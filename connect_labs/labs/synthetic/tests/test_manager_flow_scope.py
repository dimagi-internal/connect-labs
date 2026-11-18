"""The manager-flow endpoints write to the opportunity named in the body, so they
hold it to the labs-only gate themselves."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.synthetic import manager_flow_views as v


def _post(body):
    req = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    req.session = {"labs_oauth": {"access_token": "stub"}}
    req.user = MagicMock(is_authenticated=True)
    return req


@pytest.mark.parametrize(
    "view,body",
    [
        (v.manager_audit_create_api, {"opportunity_id": 10001, "flw_id": "a"}),
        (
            v.manager_coaching_attach_api,
            {"opportunity_id": 10001, "flw_id": "a", "task_id": 3, "prompt_text": "p"},
        ),
    ],
)
def test_a_labs_only_opp_the_user_cannot_open_is_refused(view, body):
    req = _post(body)
    with (
        patch.object(v, "labs_only_scope_denied_reason", return_value="no") as gate,
        patch("connect_labs.workflow.data_access.WorkflowDataAccess") as wda,
        patch("connect_labs.tasks.data_access.TaskDataAccess") as tda,
    ):
        resp = view(req, run_id=5)
    assert resp.status_code == 404
    gate.assert_called_once_with(req.user, opportunity_id=10001)
    wda.assert_not_called()
    tda.assert_not_called()
