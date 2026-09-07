"""#1169: the view-layer half of the caller-supplied opportunity hint.

`get_audit_session`'s rung 0 is only useful if the pages that already know
where a session lives actually say so. This pins the parsing — deliberately
lenient, because the value is a hint and never an authorization — and the fact
that the write endpoints read it at all.
"""

import pytest
from django.test import RequestFactory

from connect_labs.audit.views import _session_opportunity_hint


@pytest.mark.parametrize(
    "method,payload,expected",
    [
        ("post", {"storage_opportunity_id": "1978"}, 1978),
        ("get", {"storage_opportunity_id": "1978"}, 1978),
        # Absent is the normal case for every caller that doesn't know.
        ("post", {}, None),
        ("get", {}, None),
        # Blank is what a template renders when the field is null; it must read
        # as "no hint", not as a scope of 0.
        ("post", {"storage_opportunity_id": ""}, None),
        # Garbage is dropped rather than 500ing: the value arrives from a page
        # and is untrusted by construction.
        ("post", {"storage_opportunity_id": "not-a-number"}, None),
        ("get", {"storage_opportunity_id": "12; DROP TABLE"}, None),
    ],
)
def test_hint_parsing(method, payload, expected):
    rf = RequestFactory()
    request = rf.post("/", payload) if method == "post" else rf.get("/", payload)
    assert _session_opportunity_hint(request) == expected


def test_every_session_write_endpoint_reads_the_hint():
    """The save path is the one that mattered — a program-scoped auditor paid a
    full sweep on every save. Catching a new endpoint that forgets to thread it
    is cheaper here than in production timing data.
    """
    import inspect

    from connect_labs.audit import views

    for name in (
        "ExperimentSaveAuditView",
        "ExperimentAuditCompleteView",
        "ExperimentAuditUncompleteView",
        "ExperimentAuditDeleteView",
        "ExperimentApplyAssessmentResultsView",
        "ExperimentBulkAssessmentDataView",
    ):
        src = inspect.getsource(getattr(views, name))
        assert "get_audit_session" in src, f"{name} no longer looks up a session — update this test"
        assert "_session_opportunity_hint(request)" in src, (
            f"{name} looks up a session by id without passing the caller's hint; "
            "a program-scoped caller will pay a cross-opportunity sweep per request (#1169)"
        )
