"""Filtering audit sessions down to a specific set of audits, for the
Photo Verification Success Rate report.

The report is scoped to exactly the audits one auditor (e.g. Abdoul) ran
during weekly review. The auditor hands over a list of IDs, but those IDs
can be EITHER an audit session's own record id OR the bulk-image-audit
workflow-run id that created a batch of sessions -- both are drawn from the
same LabsRecord id sequence, so a caller cannot tell which kind they were
given. ``filter_audit_sessions`` therefore keeps a session when EITHER its
own id or its workflow_run_id is in the requested set, so the report works
whichever kind of id the auditor supplied. It can additionally pin the
result to a single auditor by username.
"""

import json
from unittest import mock

from django.test import RequestFactory

from connect_labs.audit.data_access import filter_audit_sessions, parse_session_id_filter
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.views import (
    AuditScopeContextAPIView,
    OpportunityAuditSessionsSummaryAPIView,
    ProgramAuditSessionsSummaryAPIView,
)


def _session(id, run_id=None, username=None, opp=1996, opp_name=None):
    return AuditSessionRecord(
        {
            "id": id,
            "experiment": "audit",
            "type": "AuditSession",
            "data": {"visit_results": {}, "opportunity_id": opp, "opportunity_name": opp_name or f"Opp {opp}"},
            "opportunity_id": opp,
            "username": username,
            "labs_record_id": run_id,
        }
    )


class TestFilterByIds:
    def test_matches_by_session_id(self):
        sessions = [_session(5064), _session(9999)]
        kept = filter_audit_sessions(sessions, ids={5064})
        assert [s.id for s in kept] == [5064]

    def test_matches_by_workflow_run_id(self):
        # The session's OWN id (81) is not in the set, but the run that
        # created it (6425) is -- it must still be kept.
        sessions = [_session(81, run_id=6425), _session(82, run_id=7000)]
        kept = filter_audit_sessions(sessions, ids={6425})
        assert [s.id for s in kept] == [81]

    def test_excludes_session_matching_neither_id_nor_run(self):
        sessions = [_session(82, run_id=7000)]
        kept = filter_audit_sessions(sessions, ids={5064, 6425})
        assert kept == []

    def test_null_workflow_run_id_is_safe(self):
        # A session with no run link must not crash and must not be matched
        # just because the set is non-empty.
        sessions = [_session(82, run_id=None)]
        kept = filter_audit_sessions(sessions, ids={5064})
        assert kept == []

    def test_no_ids_returns_all(self):
        # Backwards compatibility: an absent id filter changes nothing.
        sessions = [_session(1), _session(2)]
        assert filter_audit_sessions(sessions, ids=None) == sessions
        assert filter_audit_sessions(sessions, ids=set()) == sessions


class TestFilterByAuditor:
    def test_created_by_keeps_only_that_auditor(self):
        sessions = [_session(1, username="abdoul"), _session(2, username="someone_else")]
        kept = filter_audit_sessions(sessions, created_by="abdoul")
        assert [s.id for s in kept] == [1]

    def test_no_created_by_returns_all(self):
        sessions = [_session(1, username="abdoul"), _session(2, username="other")]
        assert filter_audit_sessions(sessions, created_by=None) == sessions


class TestFiltersCombine:
    def test_ids_and_created_by_are_anded(self):
        sessions = [
            _session(5064, username="abdoul"),  # matches both
            _session(5064 + 1, username="abdoul"),  # right auditor, wrong id
            _session(6425, username="other"),  # right id, wrong auditor -- via own id
        ]
        kept = filter_audit_sessions(sessions, ids={5064, 6425}, created_by="abdoul")
        assert [s.id for s in kept] == [5064]


class TestParseIdFilter:
    def test_parses_comma_separated_ints(self):
        assert parse_session_id_filter("5064,6425,12446") == {5064, 6425, 12446}

    def test_none_and_empty_mean_no_filter(self):
        assert parse_session_id_filter(None) is None
        assert parse_session_id_filter("") is None
        assert parse_session_id_filter("   ") is None

    def test_trims_and_ignores_blank_entries(self):
        assert parse_session_id_filter(" 5064 , 6425 ,, ") == {5064, 6425}

    def test_ignores_non_integer_entries(self):
        # A stray non-numeric token must not break the whole filter.
        assert parse_session_id_filter("5064,abc,6425") == {5064, 6425}

    def test_all_invalid_tokens_mean_no_filter(self):
        # "abc,def" carries no usable ids -- treat as no filter rather than an
        # empty set (which filter_audit_sessions would also treat as no filter).
        assert parse_session_id_filter("abc,def") is None


def _summary_response(query, sessions):
    """Drive OpportunityAuditSessionsSummaryAPIView.get with a stubbed data
    access returning ``sessions``, and return the parsed JSON body."""
    view = OpportunityAuditSessionsSummaryAPIView()
    request = RequestFactory().get("/audit/api/opportunity/1996/sessions-summary/" + query)

    stub_da = mock.MagicMock()
    stub_da.get_audit_sessions.return_value = sessions
    stub_da.get_flw_names.return_value = {}
    with mock.patch("connect_labs.audit.views.AuditDataAccess", return_value=stub_da):
        response = view.get(request, opp_id=1996)
    return json.loads(response.content)


class TestSummaryEndpointFilter:
    def test_ids_query_filters_returned_sessions(self):
        sessions = [_session(5064, username="abdoul"), _session(9999, username="abdoul")]
        body = _summary_response("?ids=5064", sessions)
        assert body["success"] is True
        assert [s["id"] for s in body["sessions"]] == [5064]

    def test_created_by_query_filters_by_auditor(self):
        sessions = [_session(1, username="abdoul"), _session(2, username="other")]
        body = _summary_response("?created_by=abdoul", sessions)
        assert [s["id"] for s in body["sessions"]] == [1]

    def test_response_exposes_auditor_username(self):
        sessions = [_session(1, username="abdoul")]
        body = _summary_response("", sessions)
        assert body["sessions"][0]["auditor_username"] == "abdoul"

    def test_response_exposes_workflow_run_id(self):
        # The report needs the run id (alongside the session id) to report
        # which of the configured audit IDs were actually matched.
        sessions = [_session(1, run_id=6425, username="abdoul")]
        body = _summary_response("", sessions)
        assert body["sessions"][0]["workflow_run_id"] == 6425

    def test_no_filters_returns_all(self):
        sessions = [_session(1, username="abdoul"), _session(2, username="other")]
        body = _summary_response("", sessions)
        assert {s["id"] for s in body["sessions"]} == {1, 2}

    def test_response_exposes_opportunity_name_for_grouping(self):
        sessions = [_session(1, opp=1996, opp_name="EHA")]
        body = _summary_response("", sessions)
        assert body["sessions"][0]["opportunity_name"] == "EHA"


def _program_summary_response(query, sessions):
    """Drive ProgramAuditSessionsSummaryAPIView.get with a stubbed program-scoped
    data access (its get_audit_sessions fans out across the program's opps)."""
    view = ProgramAuditSessionsSummaryAPIView()
    request = RequestFactory().get("/audit/api/program/25/sessions-summary/" + query)

    stub_da = mock.MagicMock()
    stub_da.get_audit_sessions.return_value = sessions
    stub_da.get_flw_names.return_value = {}
    with mock.patch("connect_labs.audit.views.AuditDataAccess", return_value=stub_da):
        response = view.get(request, program_id=25)
    return json.loads(response.content)


class TestProgramSummaryEndpoint:
    def test_returns_sessions_across_multiple_opportunities(self):
        sessions = [
            _session(1, opp=1996, opp_name="EHA", username="abdoul"),
            _session(2, opp=1997, opp_name="C3HD", username="abdoul"),
        ]
        body = _program_summary_response("", sessions)
        assert body["success"] is True
        assert {(s["id"], s["opportunity_id"], s["opportunity_name"]) for s in body["sessions"]} == {
            (1, 1996, "EHA"),
            (2, 1997, "C3HD"),
        }

    def test_scopes_data_access_by_program_id(self):
        # The program endpoint must build a PROGRAM-scoped data access so the
        # audit layer fans out across the program's member opportunities.
        with mock.patch("connect_labs.audit.views.AuditDataAccess") as DA:
            DA.return_value.get_audit_sessions.return_value = []
            DA.return_value.get_flw_names.return_value = {}
            view = ProgramAuditSessionsSummaryAPIView()
            view.get(RequestFactory().get("/audit/api/program/25/sessions-summary/"), program_id=25)
            _, kwargs = DA.call_args
            assert kwargs.get("program_id") == 25

    def test_applies_ids_and_created_by_filters(self):
        sessions = [
            _session(5064, opp=1996, username="abdoul"),
            _session(9999, opp=1997, username="abdoul"),  # off-list id
            _session(6425, opp=1997, username="other"),  # wrong auditor
        ]
        body = _program_summary_response("?ids=5064,6425&created_by=abdoul", sessions)
        assert [s["id"] for s in body["sessions"]] == [5064]


ORG_DATA = {
    "programs": [{"id": 25, "name": "Readers Nigeria"}],
    "opportunities": [
        {"id": 1996, "name": "EHA opp", "program": 25},
        {"id": 1997, "name": "C3HD opp", "program": 25},
    ],
}


def _scope_context(labs_context):
    view = AuditScopeContextAPIView()
    request = RequestFactory().get("/audit/api/scope-context/")
    request.labs_context = labs_context
    with mock.patch("connect_labs.audit.views.get_org_data", return_value=ORG_DATA):
        response = view.get(request)
    return json.loads(response.content)


class TestScopeContextEndpoint:
    def test_opportunity_context_reports_opportunity_mode_and_parent_program(self):
        body = _scope_context({"opportunity_id": 1996})
        assert body["mode"] == "opportunity"
        assert body["opportunity_id"] == 1996
        assert body["opportunity_name"] == "EHA opp"
        # parent program surfaced so the report can offer a whole-program view
        assert body["program_id"] == 25
        assert body["program_name"] == "Readers Nigeria"

    def test_program_context_reports_program_mode_no_opportunity(self):
        body = _scope_context({"program_id": 25})
        assert body["mode"] == "program"
        assert body["program_id"] == 25
        assert body["program_name"] == "Readers Nigeria"
        assert body["opportunity_id"] is None

    def test_no_context_reports_none_mode(self):
        body = _scope_context({})
        assert body["mode"] == "none"
