"""The bulk review screen's calls to a PROGRAM-OWNED workflow run.

Confirmed live 2026-09-07 on run 18803 (program 217): read 200 under
``program_id=217`` from the workflow page, then 404 nineteen seconds later under
``opportunity_id=2154`` from the bulk audit page it launched. 1,769 such 404s in
the preceding 7 days across four users.

The mechanism has three parts and they fail independently, so each is pinned:

1. ``?opportunity_id=<n>`` on this page's URL makes ``LabsContextMiddleware``
   overwrite the session's ambient scope. Every later call from the page is
   therefore opportunity-scoped -- including the param-less ones, which the
   middleware 302s back with that opportunity appended.
2. The production API exact-matches scope rather than resolving a hierarchy, so
   an opportunity-scoped lookup can never see a program-owned run.
3. All three of the page's workflow-run call sites swallow the failure, so it
   costs the completion write-back and the persisted ``flw_tasks`` without ever
   surfacing in the UI. That silence is why it ran for a week.

The fix hands the owning program back as a FALLBACK scope. The name matters:
``program_id`` is a ``labs.context.CONTEXT_PARAMS`` entry and would repoint the
session's ambient scope, breaking this page's own opportunity-scoped calls --
so the hint travels as ``owning_program_id``.
"""

from pathlib import Path
from unittest import mock

from django.test import RequestFactory

from connect_labs.audit.views import ExperimentBulkAssessmentView
from connect_labs.labs.context import CONTEXT_PARAMS

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "connect_labs" / "templates" / "audit" / "bulk_assessment.html"

OPPORTUNITY_ID = 2154  # CHC - NG - JHF - RCT
PROGRAM_ID = 217  # the program that owns run 18803


class _StubSession:
    pk = 18804
    id = 18804
    status = "in_progress"
    completed_at = None
    data: dict = {}
    notes = ""
    kpi_notes = ""
    overall_result = ""
    pass_threshold = 100
    workflow_run_id = 18803
    opportunity_id = OPPORTUNITY_ID


def _context(program: int | None = PROGRAM_ID, opportunity_id: int | None = OPPORTUNITY_ID) -> dict:
    view = ExperimentBulkAssessmentView()
    view.request = RequestFactory().get("/audit/18804/bulk/?opportunity_id=2154&workflow_run_id=18803")
    view.kwargs = {"pk": 18804}
    view.object = _StubSession()
    view.object.opportunity_id = opportunity_id

    org_data = {"opportunities": [{"id": OPPORTUNITY_ID, "organization": "jhf", "program": program}]}
    with mock.patch("connect_labs.audit.views.get_org_data", return_value=org_data):
        return view.get_context_data(object=view.object, session=view.object)


# --- The view publishes the scope the page will need ----------------------------


def test_the_owning_program_reaches_the_template():
    assert _context()["owning_program_id"] == PROGRAM_ID


def test_an_opportunity_with_no_resolvable_program_publishes_none():
    """No hint is the pre-fix behaviour, which is correct when there is no
    program to fall back to -- an opportunity-owned run is found by the scope the
    page already has."""
    assert _context(program=None)["owning_program_id"] is None
    assert _context(opportunity_id=None)["owning_program_id"] is None


# --- The template spends it on every workflow-run call --------------------------


def test_the_hint_is_not_a_labs_context_param():
    """The whole reason for the name. If this were ``program_id`` the page would
    repoint its own ambient scope on the first call and start missing the
    opportunity-scoped records it exists to show."""
    assert "owning_program_id" not in CONTEXT_PARAMS


def test_the_read_carries_the_hint_on_the_querystring():
    html = TEMPLATE_PATH.read_text()
    assert "const runScopeQuery = owningProgramId ? '?owning_program_id=' + owningProgramId : '';" in html
    assert "fetch('/labs/workflow/api/run/' + workflowRunId + '/' + runScopeQuery)" in html


def test_every_state_write_carries_the_hint_in_its_body():
    """POSTs cannot use the querystring: the middleware only decorates GETs, and
    a redirect cannot preserve a POST body. Both write sites -- task creation and
    completion -- were losing their write silently, so both are pinned."""
    html = TEMPLATE_PATH.read_text()
    state_writes = html.count("/state/'")
    assert state_writes == 2, f"expected 2 state-write call sites, found {state_writes}"
    assert html.count("owning_program_id: owningProgramId,") == state_writes


def test_no_workflow_run_call_is_left_unscoped():
    """The regression guard. A fourth call site added without the hint would 404
    the same way and, like the first three, say nothing about it."""
    html = TEMPLATE_PATH.read_text()
    unscoped = "fetch('/labs/workflow/api/run/' + workflowRunId + '/')"
    assert unscoped not in html
