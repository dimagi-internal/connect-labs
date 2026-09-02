"""The "AI Flagged" entry on the bulk review screen's Status filter.

Two halves, and they fail in different ways.

The **view** half picks which status the page opens on. It is scoped to one
program on purpose: `bulk_assessment.html` is the shared review screen for every
audit workflow in labs, so defaulting to "AI Flagged" everywhere would open an
audit with no AI classifiers on a filter that hides every image in it -- a blank
review screen with nothing to say why. #1385 removed the last hardcoded ids from
this file, so the gate lives on one named constant rather than a probe.

The **template** half is the filter itself. `isAiFlagged` reads `status`, and it
is safe only because the view normalises that field to a closed vocabulary: one
of pass/fail/duplicate_fake/duplicate/fake, or else the literal "pending". A new
verdict value added to that set without a thought for this predicate lands in
"AI Flagged" silently, so the vocabulary is pinned here too.
"""

import re
from pathlib import Path
from unittest import mock

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from connect_labs.audit.views import AI_FLAGGED_DEFAULT_PROGRAM_ID, ExperimentBulkAssessmentView

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "connect_labs" / "templates" / "audit" / "bulk_assessment.html"

OPPORTUNITY_ID = 2154  # CHC - NG - JHF - RCT, a member of the gated program.


class _StubSession:
    """Only what get_context_data and the template's {% url %} tags read."""

    pk = 1
    id = 1
    status = "in_progress"
    completed_at = None
    data: dict = {}
    notes = ""
    kpi_notes = ""
    overall_result = ""
    pass_threshold = 100
    workflow_run_id = None
    opportunity_id = OPPORTUNITY_ID


def _context(query: str = "", program: int | None = AI_FLAGGED_DEFAULT_PROGRAM_ID) -> dict:
    view = ExperimentBulkAssessmentView()
    view.request = RequestFactory().get("/audit/1/bulk/" + query)
    view.kwargs = {"pk": 1}
    view.object = _StubSession()

    org_data = {"opportunities": [{"id": OPPORTUNITY_ID, "organization": "jhf", "program": program}]}
    with mock.patch("connect_labs.audit.views.get_org_data", return_value=org_data):
        return view.get_context_data(object=view.object, session=view.object)


# --- Which status the page opens on ---------------------------------------------


def test_the_gated_program_opens_on_ai_flagged():
    assert _context()["selected_status"] == "ai_flagged"


def test_every_other_program_still_opens_on_all():
    """The blast-radius guarantee. This screen serves every audit workflow, and
    one without AI classifiers must not open on a filter that hides all of it."""
    assert _context(program=176)["selected_status"] == "all"


def test_an_opportunity_with_no_resolvable_program_opens_on_all():
    """A session whose opportunity is missing from the caller's org data resolves
    no program at all. That is the non-AI case by default, not the gated one."""
    assert _context(program=None)["selected_status"] == "all"


@pytest.mark.parametrize("requested", ["all", "pending", "pass", "fail", "duplicate_fake"])
def test_an_explicit_status_in_the_url_beats_the_default(requested):
    """`?status=all` is a reviewer asking for everything on purpose, and is the
    reason the view distinguishes an absent param from an empty one -- reading
    `GET.get("status", "all")` would make the two indistinguishable and the
    default unreachable."""
    assert _context(f"?status={requested}")["selected_status"] == requested


def test_a_blank_status_param_falls_back_to_the_default():
    assert _context("?status=")["selected_status"] == "ai_flagged"


def test_the_default_is_published_separately_from_the_selection():
    """The template compares the two to decide whether the URL needs a ?status=
    at all, so a page that opens on its default keeps a clean URL while an
    explicit choice survives a reload."""
    ctx = _context()
    assert ctx["default_status"] == "ai_flagged"
    assert _context(program=176)["default_status"] == "all"
    assert _context("?status=fail")["default_status"] == "ai_flagged"


# --- The filter itself ----------------------------------------------------------


def test_the_dropdown_offers_ai_flagged():
    html = TEMPLATE_PATH.read_text()
    assert '<option value="ai_flagged">AI Flagged</option>' in html


def test_the_filter_routes_ai_flagged_to_its_own_predicate():
    """Guards the wiring, not the rule: an `ai_flagged` selection that fell
    through to the equality branch would match nothing, and the dropdown entry
    would look present and do nothing."""
    html = TEMPLATE_PATH.read_text()
    assert "isAiFlagged(assessment.status)" in html
    assert re.search(r"function isAiFlagged\(status\)", html)


def test_ai_flagged_excludes_exactly_passed_and_unreviewed():
    """The vocabulary contract with views.py's status normalisation.

    `status` is built as `result if result in {...} else "pending"`, so these six
    values are all the predicate can ever see. If a seventh is added there, this
    fails and whoever added it decides whether it belongs in AI Flagged -- rather
    than it quietly appearing in the filter.
    """

    # Mirrors isAiFlagged in bulk_assessment.html.
    def is_ai_flagged(status: str) -> bool:
        return status not in ("pass", "pending")

    emitted_by_the_view = ["pending", "pass", "fail", "duplicate_fake", "duplicate", "fake"]
    flagged = {s for s in emitted_by_the_view if is_ai_flagged(s)}

    assert flagged == {"fail", "duplicate_fake", "duplicate", "fake"}
    # Stated explicitly because these two are the whole point of the filter.
    assert not is_ai_flagged("pass")
    assert not is_ai_flagged("pending")


def test_the_status_vocabulary_has_not_grown_since_the_predicate_was_written():
    """Reads the normalisation back out of views.py, so the list above cannot
    drift away from the code it claims to mirror."""
    source = (Path(__file__).resolve().parents[1] / "views.py").read_text()
    sets = re.findall(r"result_value in \{([^}]+)\}", source)
    assert sets, "the status normalisation in views.py no longer looks like a set literal"
    for literal in sets:
        assert {v.strip().strip('"') for v in literal.split(",")} == {
            "pass",
            "fail",
            "duplicate_fake",
            "duplicate",
            "fake",
        }


# --- The inline-script contract (see test_bulk_assessment_template_html_integrity) ---

_DEFAULT_LINE = re.compile(r"const defaultStatus = (.+);")


def _rendered_default_literal(context) -> str:
    html = render_to_string("audit/bulk_assessment.html", {"session": _StubSession(), **context})
    match = _DEFAULT_LINE.search(html)
    assert match, "defaultStatus is no longer emitted to the page"
    return match.group(1).strip()


def test_the_default_status_reaches_the_page_as_a_js_string():
    assert _rendered_default_literal({"default_status": "ai_flagged"}) == "'ai_flagged'"


def test_a_missing_default_status_still_emits_valid_javascript():
    """Without the |default an absent context var renders `const x = '';` here
    rather than `const x = ;`, because the value is quoted -- but 'all' is the
    honest fallback: it is what every ungated audit uses, and it keeps
    updateUrlParams behaving exactly as it did before this filter existed."""
    assert _rendered_default_literal({}) == "'all'"
