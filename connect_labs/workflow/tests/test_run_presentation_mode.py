"""Presentation mode (``?present=1``) on a workflow run page — connect-labs#1295.

These are **capture** tests, not template-reading tests. Each one renders the
real ``workflow/run.html`` -> ``base.html`` -> ``layouts/header.html`` ->
``labs/context_selector.html`` chain and asserts against the produced DOM, for
the reason the issue gives: three of the four offending strings come from
templates *above* run.html, and one of them (the raw opportunity id) was
unreachable from a child template until this change added ``{% block header %}``.
A test that read run.html alone would have seen none of that.

The CHROME_* strings below are the literal substrings captured from the live
page named in the issue
(``/labs/workflow/5227/run/?run_id=5232&opportunity_id=10046``), so a shell
refactor that renames them fails these tests rather than silently narrowing
what presentation mode covers.
"""

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from connect_labs.labs.presentation import is_present_mode

# Builder chrome that a funder/partner link must not show. Verbatim from the
# live capture of the run page in connect-labs#1295.
CHROME_BREADCRUMB = ">Workflows</a>"
CHROME_OPPS_LABEL = ">Opportunities:<"
CHROME_OPPS_COUNT = "pickedOppIds.length + ' selected'"
CHROME_EDIT_BUTTON = 'fa-pen-to-square"></i> Edit'
CHROME_EDIT_MODAL = "Edit Opportunities"
CHROME_CONTEXT_OPP = ">Opp:<"
CHROME_RAW_OPP_ID = "(id: 10046"
CHROME_VISIT_COUNT = "visits: 276"
CHROME_HEADER_EL = '<header class="fixed top-0 left-16'
CHROME_LOGOUT = "labs/logout"
CHROME_SIDENAV_GUTTER = "justify-center pl-16 bg-stone-100"

ALL_CHROME = [
    CHROME_BREADCRUMB,
    CHROME_OPPS_LABEL,
    CHROME_OPPS_COUNT,
    CHROME_EDIT_BUTTON,
    CHROME_EDIT_MODAL,
    CHROME_CONTEXT_OPP,
    CHROME_RAW_OPP_ID,
    CHROME_VISIT_COUNT,
    CHROME_HEADER_EL,
    CHROME_LOGOUT,
    CHROME_SIDENAV_GUTTER,
]

# The workflow's own output. Presentation mode is a chrome concern only — this
# must survive, or the mode is useless.
RENDER_MOUNT = 'id="workflow-root"'


class _User:
    """Minimal authenticated user. The header renders the context selector only
    for an authenticated request, so an AnonymousUser would make these tests
    pass for the wrong reason."""

    username = "ace"
    is_authenticated = True
    is_anonymous = False
    first_name = "Ace"
    last_name = ""
    email = "ace@dimagi-ai.com"

    def get_full_name(self):
        return "Ace"

    def __str__(self):
        return "ace"


def _capture(query: str) -> str:
    """Render the real template chain for the issue's run URL and return the DOM."""
    request = RequestFactory().get(f"/labs/workflow/5227/run/{query}")
    request.user = _User()
    request.labs_context = {
        "opportunity_id": 10046,
        "opportunity": {
            "name": "Bednet Check — Two-Visit Household Spot-Check (SYNTHETIC)",
            "id": 10046,
            "visit_count": 276,
        },
    }
    context = {
        "has_context": True,
        "present_mode": is_present_mode(request),
        "definition": {"name": "Bednet Spot-Check — Programme Measurement"},
        "render_code": {"component_code": "function WorkflowUI() {}"},
        "workflow_data": {
            "multi_opp": True,
            "opportunity_ids": [10046],
            "definition_id": 5227,
            "apiEndpoints": {"updateOpportunityIds": "/labs/workflow/api/x/"},
        },
        "user_opportunities": [],
        "user_organizations": [],
        "user_programs": [],
    }
    return render_to_string("workflow/run.html", context, request=request)


@pytest.mark.django_db
def test_baseline_capture_still_shows_every_piece_of_chrome():
    """The control. Without the parameter nothing changes — and if this ever
    goes green by chrome disappearing on its own, the suppression test below
    stops proving anything."""
    dom = _capture("?run_id=5232&opportunity_id=10046")
    missing = [c for c in ALL_CHROME if c not in dom]
    assert not missing, f"chrome vanished from the DEFAULT render: {missing}"
    assert RENDER_MOUNT in dom


@pytest.mark.django_db
def test_present_mode_capture_suppresses_every_piece_of_chrome():
    dom = _capture("?run_id=5232&opportunity_id=10046&present=1")
    leaked = [c for c in ALL_CHROME if c in dom]
    assert not leaked, f"builder chrome leaked into a shared presentation link: {leaked}"


@pytest.mark.django_db
def test_present_mode_keeps_the_workflow_output_and_names_the_page():
    """Suppressing the shell must not suppress the report, and must not leave
    the page headless — the breadcrumb was the only thing naming it."""
    dom = _capture("?run_id=5232&opportunity_id=10046&present=1")
    assert RENDER_MOUNT in dom
    assert "Bednet Spot-Check — Programme Measurement" in dom


@pytest.mark.django_db
def test_present_mode_leaks_no_raw_database_id_anywhere_in_the_dom():
    """Stated as its own assertion because it is the confidentiality half of
    the issue, and it must hold against the WHOLE document, not just the
    strings enumerated above."""
    dom = _capture("?run_id=5232&opportunity_id=10046&present=1")
    assert "(id: 10046" not in dom
    assert "visits: 276" not in dom


@pytest.mark.django_db
def test_unknown_parameter_values_render_normal_chrome():
    """A typo must degrade to today's page, not to a half-suppressed one."""
    dom = _capture("?run_id=5232&opportunity_id=10046&present=maybe")
    assert CHROME_BREADCRUMB in dom
    assert CHROME_CONTEXT_OPP in dom


@pytest.mark.django_db
def test_base_html_header_include_is_an_overridable_block():
    """The prerequisite the issue names. ``labs/context_selector.html`` is
    included by ``layouts/header.html``, which base.html included inline and
    outside any block — so no child template could suppress the raw opportunity
    id. Assert the override point exists by exercising it, not by grepping."""
    from django.template import Context, Template

    dom = Template("{% extends 'base.html' %}{% block header %}HEADER-OVERRIDDEN{% endblock %}").render(Context({}))
    assert "HEADER-OVERRIDDEN" in dom
    assert CHROME_HEADER_EL not in dom


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_is_present_mode_accepts_truthy_spellings(raw):
    assert is_present_mode(RequestFactory().get("/x/", {"present": raw})) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "", "maybe"])
def test_is_present_mode_rejects_everything_else(raw):
    assert is_present_mode(RequestFactory().get("/x/", {"present": raw})) is False


def test_is_present_mode_absent_parameter():
    assert is_present_mode(RequestFactory().get("/x/")) is False


def test_is_present_mode_tolerates_a_request_with_no_get():
    assert is_present_mode(object()) is False


# --- View wiring -----------------------------------------------------------
# The capture tests above are handed `present_mode` directly, so on their own
# they would still pass if WorkflowRunView never set it. These two close that
# half: the flag reaches the template context from the request, and it survives
# the redirect the view issues on a bare run URL.


def _run_view_context(query: str):
    from unittest.mock import patch

    from connect_labs.workflow import views as views_mod

    request = RequestFactory().get(f"/labs/workflow/5227/run/{query}")
    request.session = {"labs_oauth": {"access_token": "stub-token"}}
    request.user = _User()
    # No opportunity in context, so get_context_data returns before touching
    # WorkflowDataAccess. present_mode is assigned above that early return
    # deliberately — an error page reached from a shared link must carry the
    # same chrome as the page the recipient was sent to.
    request.labs_context = {}

    view = views_mod.WorkflowRunView()
    view.request = request
    view.kwargs = {"definition_id": 5227}
    with patch.object(views_mod, "get_org_data", return_value={"opportunities": []}):
        return view.get_context_data()


def test_view_sets_present_mode_from_the_query_parameter():
    assert _run_view_context("?present=1")["present_mode"] is True


def test_view_leaves_present_mode_false_by_default():
    assert _run_view_context("")["present_mode"] is False


def test_opportunity_recovery_redirect_preserves_the_present_parameter():
    """A share link whose opportunity_id the middleware refused (a copy-paste
    with trailing text, say) is redirected to the canonical URL. That redirect
    rebuilds the query string, so it is the one place `present` could silently
    fall off a link already in someone's inbox."""
    from unittest.mock import patch

    from connect_labs.workflow import views as views_mod

    request = RequestFactory().get("/labs/workflow/5227/run/?run_id=5232&opportunity_id=10046%20stacked&present=1")
    request.session = {"labs_oauth": {"access_token": "stub-token"}}
    request.user = _User()
    request.labs_context = {}  # middleware dropped the unparseable opp id

    view = views_mod.WorkflowRunView()
    view.kwargs = {"definition_id": 5227}
    with patch.object(views_mod.WorkflowRunView, "_recover_opportunity_id", return_value=10046):
        response = view.get(request)

    assert response.status_code == 302
    assert "present=1" in response.url
    assert "opportunity_id=10046" in response.url


def test_presentation_mode_is_scoped_to_the_run_page():
    """Deliberate scope note, asserted so it cannot drift into an accidental
    half-feature: the bare-run-URL bounce lands on the workflow LIST, which has
    no presentation mode, so `present` is NOT carried there. Suppressing chrome
    on a page that still renders every other workflow would be theatre."""
    from unittest.mock import patch

    from connect_labs.workflow import views as views_mod

    request = RequestFactory().get("/labs/workflow/5227/run/?opportunity_id=10046&present=1")
    request.session = {"labs_oauth": {"access_token": "stub-token"}}
    request.user = _User()
    request.labs_context = {"opportunity_id": 10046}

    view = views_mod.WorkflowRunView()
    view.kwargs = {"definition_id": 5227}
    with patch.object(views_mod, "get_org_data", return_value={"opportunities": []}):
        response = view.get(request)

    assert response.status_code == 302
    assert response.url.startswith("/labs/workflow/?")
    assert "present" not in response.url
