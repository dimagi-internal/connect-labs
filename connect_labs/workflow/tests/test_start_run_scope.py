"""Pure unit tests for `_resolve_run_scope` — the start-run scope decision.

No DB, no LabsRecord API. Program-owned workflows (session context has
``program_id`` and no ``opportunity_id``) start PROGRAM-scoped runs; everything
else starts opp-scoped runs (session opp, or a POST/GET opp fallback).
"""

from connect_labs.workflow.views import _resolve_run_scope


def test_program_context_yields_program_scope():
    assert _resolve_run_scope({"program_id": 176}) == ("program", 176)


def test_program_context_coerces_to_int():
    assert _resolve_run_scope({"program_id": "176"}) == ("program", 176)


def test_session_opp_wins_over_program_when_both_present():
    # Opp view can carry both; the opportunity is the run owner.
    assert _resolve_run_scope({"program_id": 176, "opportunity_id": 1973}) == ("opportunity", 1973)


def test_session_opp_yields_opp_scope():
    assert _resolve_run_scope({"opportunity_id": 1973}) == ("opportunity", 1973)


def test_post_opp_fallback_when_no_context():
    assert _resolve_run_scope({}, post_opp="1973") == ("opportunity", 1973)


def test_get_opp_fallback_when_no_context():
    assert _resolve_run_scope({}, get_opp="1976") == ("opportunity", 1976)


def test_post_opp_preferred_over_get_opp():
    assert _resolve_run_scope({}, post_opp="1973", get_opp="1976") == ("opportunity", 1973)


def test_nothing_resolvable_returns_none():
    assert _resolve_run_scope({}) == (None, None)


def test_bad_opp_fallback_returns_none():
    assert _resolve_run_scope({}, post_opp="not-an-int") == (None, None)


def test_program_view_ignores_stale_opp_fallback():
    # A program-view request resolves to a program run even if a stray opp
    # param rides along — the program context takes precedence.
    assert _resolve_run_scope({"program_id": 176}, post_opp="1973") == ("program", 176)
