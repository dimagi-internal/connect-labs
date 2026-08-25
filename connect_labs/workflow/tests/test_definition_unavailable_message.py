"""A workflow you may not read must not be reported as one that does not exist.

Upstream, ``_get_opportunity_or_404`` / ``_get_program_or_404`` filter by the
caller's org membership and raise ``NotFound``, so "not authorized" and "not
found" arrive as the same 404. This view reported the second.

Prod, 2026-08-25: a user signed in under a personal address holding
opportunity access but not program access opened a program-scoped link
(``/labs/workflow/13007/run/?run_id=13669&program_id=217``), was told the
workflow did not exist, and it was escalated as a missing workflow. The record
was present the whole time -- readable under ``program_id=217`` by anyone in
that program -- and the same user loaded a different workflow successfully at
opportunity scope 43 minutes later. (#1280)
"""

from __future__ import annotations

from connect_labs.workflow.views import _definition_unavailable_message, _scope_is_reachable


class _Request:
    """Minimal stand-in: the helper only reads session org data."""

    def __init__(self, programs=(), opportunities=()):
        self.session = {
            "labs_oauth": {"organization_data": {"programs": list(programs), "opportunities": list(opportunities)}}
        }
        self.user = None


def test_program_the_viewer_cannot_reach_is_named_as_access():
    request = _Request(programs=[{"id": 176}], opportunities=[{"id": 2154}])

    message = _definition_unavailable_message(request, 13007, None, 217)

    assert "don't have access to program 217" in message
    assert "not found" not in message.lower()


def test_it_suggests_the_wrong_login_which_is_what_actually_happened():
    request = _Request(programs=[], opportunities=[{"id": 2154}])

    message = _definition_unavailable_message(request, 13007, None, 217)

    assert "more than one login" in message


def test_opportunity_the_viewer_cannot_reach_is_named_as_access():
    request = _Request(opportunities=[{"id": 2154}])

    message = _definition_unavailable_message(request, 16018, 2156, None)

    assert "don't have access to opportunity 2156" in message


def test_reachable_scope_still_reports_a_genuine_miss():
    """Access is fine, so the record really is absent -- say so, and name the scope."""
    request = _Request(programs=[{"id": 217}])

    message = _definition_unavailable_message(request, 99999, None, 217)

    assert "not found under program 217" in message


def test_program_is_checked_before_opportunity():
    """A program-scoped run has no owning opportunity; don't blame the wrong scope."""
    request = _Request(programs=[], opportunities=[])

    message = _definition_unavailable_message(request, 13007, None, 217)

    assert "program 217" in message
    assert "opportunity" not in message.split("program 217")[0]


def test_string_and_int_ids_both_resolve():
    """labs_context passes ids through from a URL, so they can arrive as strings."""
    request = _Request(programs=[{"id": 217}])

    assert _scope_is_reachable(request, "programs", "217")
    assert _scope_is_reachable(request, "programs", 217)
    assert not _scope_is_reachable(request, "programs", 218)


def test_absent_scope_is_never_reachable():
    assert not _scope_is_reachable(_Request(programs=[{"id": 217}]), "programs", None)
