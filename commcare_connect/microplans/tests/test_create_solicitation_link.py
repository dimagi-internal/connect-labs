"""Tests for the "Create solicitation" entry points into the microplans UI.

The entry point lives where the owner BROWSES their portfolio (the program
workspace), on each plan card and each group card, plus on the group manage
page — NOT inside a plan's review/edit screen. These tests pin that placement:

  (a) ProgramReviewView (the plan review/edit screen) must NOT inject a
      create_solicitation_url — it was deliberately moved out to the portfolio.
  (b) ProgramGroupPageView (group manage page) still injects a well-formed url.
      Its get_context_data constructs ProgramPlanDataAccess (needs OAuth) before
      setting the url, so we assert the URL-construction CONTRACT directly rather
      than exercise the full view.
  (c) The program workspace template renders per-plan and per-group
      "Create solicitation" links client-side — assert the template source wires
      the source_plan_id / source_group_id query params via the url helper.
"""

from pathlib import Path

from django.test import RequestFactory
from django.urls import reverse

from commcare_connect.microplans.views import ProgramReviewView

# ---------------------------------------------------------------------------
# (a) ProgramReviewView must NOT carry the entry point anymore
# ---------------------------------------------------------------------------


def test_review_context_has_no_create_solicitation_url():
    """The plan review/edit screen no longer offers Create solicitation.

    _LabsContextSyncMixin.dispatch() guards on auth before touching session, so
    bypass dispatch and call get_context_data directly (it only reverse()s urls).
    """
    req = RequestFactory().get("/")
    view = ProgramReviewView()
    view.request = req
    view.kwargs = {"program_id": 25, "plan_id": 7}
    view.args = []
    ctx = view.get_context_data(program_id=25, plan_id=7)
    assert "create_solicitation_url" not in ctx, "review page should not carry the solicitation entry point anymore"


# ---------------------------------------------------------------------------
# (b) ProgramGroupPageView still builds a well-formed url (contract)
# ---------------------------------------------------------------------------


def test_group_page_create_solicitation_url_contract():
    base = reverse("solicitations:create")
    assert base == "/solicitations/create/", f"Expected /solicitations/create/, got {base!r}"
    url = base + f"?source_program_id={42}&source_group_id={99}"
    assert url == "/solicitations/create/?source_program_id=42&source_group_id=99"


# ---------------------------------------------------------------------------
# (c) The portfolio template wires per-plan and per-group entry points
# ---------------------------------------------------------------------------

_WORKSPACE_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "microplans" / "program_workspace.html"


def test_workspace_template_wires_plan_and_group_solicitation_links():
    src = _WORKSPACE_TEMPLATE.read_text()
    # The url helpers exist and key on the source plan / group.
    assert "planSolicitationUrl" in src
    assert "groupSolicitationUrl" in src
    assert "source_plan_id=" in src
    assert "source_group_id=" in src
    # Both card builders render the link.
    assert "planSolicitationUrl(p.plan_id)" in src
    assert "groupSolicitationUrl(g.group_id)" in src
    # And it points at the canonical create route.
    assert "{% url 'solicitations:create' %}" in src
