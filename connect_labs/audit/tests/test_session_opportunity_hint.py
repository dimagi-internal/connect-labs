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
        # The default name list is exactly one entry, so the PAGE's own
        # ?opportunity_id= must NOT leak into the write endpoints' behaviour.
        ("get", {"opportunity_id": "2154"}, None),
    ],
)
def test_hint_parsing(method, payload, expected):
    rf = RequestFactory()
    request = rf.post("/", payload) if method == "post" else rf.get("/", payload)
    assert _session_opportunity_hint(request) == expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        # The bulk PAGE is linked as /audit/<id>/bulk/?opportunity_id=<n>, so this
        # is the name its scope actually arrives under.
        ({"opportunity_id": "2154"}, 2154),
        # The explicit name still wins when both are present.
        ({"storage_opportunity_id": "1978", "opportunity_id": "2154"}, 1978),
        # An unparseable first name falls through rather than swallowing the hint.
        ({"storage_opportunity_id": "junk", "opportunity_id": "2154"}, 2154),
        ({}, None),
    ],
)
def test_hint_reads_the_page_scope_when_asked_to(payload, expected):
    request = RequestFactory().get("/", payload)
    assert _session_opportunity_hint(request, ("storage_opportunity_id", "opportunity_id")) == expected


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


def test_the_bulk_page_itself_passes_the_hint():
    """The PAGE was the expensive one, and the guard above could never catch it.

    That test enumerates the write endpoints, all of which take a bare `request`.
    ExperimentBulkAssessmentView is a DetailView using `self.request`, so it was
    invisible to that assertion AND was the only session lookup in the module with
    no rung-0 hint at all -- while being the lookup that can least afford one,
    since it runs before the page exists and has nothing resolved to reuse.

    Measured 2026-09-07: 408-411 upstream calls per page open, 54-86 s each.
    """
    import inspect

    from connect_labs.audit import views

    src = inspect.getsource(views.ExperimentBulkAssessmentView)
    assert "get_audit_session" in src, "the bulk page no longer looks up a session — update this test"
    assert "_session_opportunity_hint(" in src, (
        "ExperimentBulkAssessmentView looks up a session by id without passing the "
        "caller's hint; a program-scoped auditor pays one upstream request per "
        "candidate opportunity on EVERY page open (#1169)"
    )
    assert "opportunity_id" in src, (
        "the bulk page must read the ?opportunity_id= its own links set, not only "
        "the storage_opportunity_id its follow-up calls use (#1169)"
    )
