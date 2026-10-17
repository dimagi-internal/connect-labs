"""A save that Connect REFUSES must not come back as a labs 500 (#2575).

2026-10-16: Connect answered `update_record` with 403 for one audit session.
The view turned every refusal into "500 An internal error occurred", and the
page's autosave retried on a fixed 1.5s timer, so one open tab sent ~1,200
refused saves an hour and paged labs-jj-alb-target-5xx-high. The upstream 4xx
now passes through, which is what lets the page stop retrying.
"""

import pytest

from connect_labs.audit.tests.test_save_audit_notes import _make_session, labs_client  # noqa: F401
from connect_labs.labs.integrations.connect.api_client import LabsAPIError


def _patch_failing_save(monkeypatch, session, exc):
    from connect_labs.audit import views

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, opportunity_id=None):
            return session

        def save_audit_session(self, s):
            raise exc

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)


@pytest.mark.parametrize("status", [403, 400])
def test_upstream_refusal_passes_through_as_4xx(labs_client, monkeypatch, status):  # noqa: F811
    session = _make_session()
    _patch_failing_save(monkeypatch, session, LabsAPIError("refused", status_code=status, body="<html>nope</html>"))

    response = labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})

    assert response.status_code == status
    body = response.json()
    assert body["upstream_status"] == status
    assert "NOT saved" in body["error"]


@pytest.mark.parametrize(
    "exc",
    [
        LabsAPIError("bad gateway", status_code=502),
        LabsAPIError("network", status_code=None, upstream_error="ConnectError"),
        RuntimeError("labs bug"),
    ],
)
def test_upstream_5xx_network_and_labs_faults_stay_500(labs_client, monkeypatch, exc):  # noqa: F811
    session = _make_session()
    _patch_failing_save(monkeypatch, session, exc)

    response = labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})

    assert response.status_code == 500
