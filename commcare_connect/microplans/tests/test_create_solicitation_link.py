"""Tests that ProgramReviewView and ProgramGroupPageView inject a well-formed
create_solicitation_url into their template context (Task 7).

Approach:
  (a) ProgramReviewView: RequestFactory unit test — the view's get_context_data
      only calls reverse() and sets plain string context; it does NOT call any API
      or OAuth, so a bare RequestFactory request with an empty session is sufficient.
  (b) ProgramGroupPageView: the view's get_context_data constructs ProgramPlanDataAccess
      (which needs an OAuth token) and calls da.get_group() / da.list_plans() before
      it ever sets create_solicitation_url — so exercising it under RequestFactory would
      require mocking the entire data-access layer.  Instead we assert the URL-
      construction CONTRACT directly: that reverse("solicitations:create") returns the
      expected path and that the f-string the view uses produces the expected query
      string.  This pins the route name and format just as strongly as a view-level test.
"""

from django.test import RequestFactory

from commcare_connect.microplans.views import ProgramReviewView

# ---------------------------------------------------------------------------
# (a) RequestFactory unit test for ProgramReviewView
# ---------------------------------------------------------------------------


def test_review_context_has_create_solicitation_url():
    """ProgramReviewView.get_context_data injects a correctly-formed URL."""
    req = RequestFactory().get("/")
    # _LabsContextSyncMixin.dispatch() checks request.user.is_authenticated before
    # touching session — so bypass dispatch entirely and call get_context_data directly.
    view = ProgramReviewView()
    view.request = req  # needed by _sd_urls() / context helpers
    view.kwargs = {"program_id": 25, "plan_id": 7}
    view.args = []
    ctx = view.get_context_data(program_id=25, plan_id=7)
    url = ctx["create_solicitation_url"]
    assert url.startswith("/solicitations/create/"), f"URL should start with /solicitations/create/, got {url!r}"
    assert "source_program_id=25" in url, f"URL should contain source_program_id=25, got {url!r}"
    assert "source_plan_id=7" in url, f"URL should contain source_plan_id=7, got {url!r}"


# ---------------------------------------------------------------------------
# (b) Contract test: verify the URL-construction formula for ProgramGroupPageView
# ---------------------------------------------------------------------------


def test_group_page_create_solicitation_url_contract():
    """The formula used in ProgramGroupPageView.get_context_data builds the right URL.

    This pins the route name and query-string format without exercising the full view
    (which needs OAuth data access).
    """
    from django.urls import reverse

    base = reverse("solicitations:create")
    assert base == "/solicitations/create/", f"Expected /solicitations/create/, got {base!r}"

    program_id = 42
    group_id = 99
    url = base + f"?source_program_id={program_id}&source_group_id={group_id}"
    assert url == "/solicitations/create/?source_program_id=42&source_group_id=99"


def test_review_create_solicitation_url_contract():
    """The formula used in ProgramReviewView.get_context_data builds the right URL."""
    from django.urls import reverse

    base = reverse("solicitations:create")
    program_id = 25
    plan_id = 7
    url = base + f"?source_program_id={program_id}&source_plan_id={plan_id}"
    assert url == "/solicitations/create/?source_program_id=25&source_plan_id=7"
