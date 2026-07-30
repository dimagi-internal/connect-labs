"""
Tests for run_single_ai_review_with_notes -- the AIReviewAPIView previously
always sent back ai_notes="" for successful/failed reviews (only errors got a
real message), silently discarding whatever badge_label/pass_label/reason an
agent returned. This covers that the new helper actually surfaces it, while
run_single_ai_review (used by the async task path) is untouched.
"""

from connect_labs.audit.ai_review import run_single_ai_review, run_single_ai_review_with_notes
from connect_labs.labs.ai_review_agents.types import ReviewResult


class _PassAgent:
    def review(self, ctx):
        return ReviewResult.success(pass_label="Looks good (91%)")


class _FailAgent:
    def review(self, ctx):
        return ReviewResult.failure(badge_label="Wrong product (68%)")


class _FailAgentNoBadgeLabel:
    def review(self, ctx):
        return ReviewResult.failure(reason="fell back to reason")


class _ErrorAgent:
    def review(self, ctx):
        return ReviewResult.error("boom")


class _RaisingAgent:
    def review(self, ctx):
        raise RuntimeError("network blew up")


class TestRunSingleAiReviewWithNotes:
    def test_pass_surfaces_pass_label(self):
        ai_result, ai_notes = run_single_ai_review_with_notes(_PassAgent(), b"img")
        assert ai_result == "match"
        assert ai_notes == "Looks good (91%)"

    def test_failure_surfaces_badge_label(self):
        ai_result, ai_notes = run_single_ai_review_with_notes(_FailAgent(), b"img")
        assert ai_result == "no_match"
        assert ai_notes == "Wrong product (68%)"

    def test_failure_falls_back_to_reason_when_no_badge_label(self):
        ai_result, ai_notes = run_single_ai_review_with_notes(_FailAgentNoBadgeLabel(), b"img")
        assert ai_result == "no_match"
        assert ai_notes == "fell back to reason"

    def test_error_result_surfaces_first_error_message(self):
        ai_result, ai_notes = run_single_ai_review_with_notes(_ErrorAgent(), b"img")
        assert ai_result == "error"
        assert ai_notes == "boom"

    def test_exception_surfaces_exception_message(self):
        ai_result, ai_notes = run_single_ai_review_with_notes(_RaisingAgent(), b"img")
        assert ai_result == "error"
        assert "network blew up" in ai_notes

    def test_run_single_ai_review_unchanged_bare_string_return(self):
        # The async-task-facing helper must keep returning a bare string --
        # this new function is additive, not a replacement.
        assert run_single_ai_review(_PassAgent(), b"img") == "match"
        assert run_single_ai_review(_FailAgent(), b"img") == "no_match"
