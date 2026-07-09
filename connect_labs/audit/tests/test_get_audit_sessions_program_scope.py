"""Regression coverage for the "some but not all audits show under the
program" bug: get_audit_sessions() must fan out across every opportunity in
a program-scoped AuditDataAccess and merge results, since audit sessions are
opportunity-tagged (not program-tagged) and the production API does a
literal field match rather than resolving the opportunity->program
hierarchy.
"""

from unittest.mock import MagicMock, patch


class FakeSession:
    def __init__(self, id, opportunity_id, labs_record_id=None):
        self.id = id
        self.opportunity_id = opportunity_id
        self.labs_record_id = labs_record_id


class TestGetAuditSessionsProgramScope:
    def test_fans_out_across_program_member_opportunities(self):
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_get_org_data.return_value = {
                "opportunities": [
                    {"id": 1973, "program": 176},
                    {"id": 1976, "program": 176},
                    {"id": 9999, "program": 999},  # different program — must be excluded
                ]
            }

            program_scoped_client = MagicMock()
            program_scoped_client.get_records.return_value = []  # nothing tagged program_id itself

            opp_1973_client = MagicMock()
            opp_1973_client.get_records.return_value = [FakeSession(id=1, opportunity_id=1973)]

            opp_1976_client = MagicMock()
            opp_1976_client.get_records.return_value = [FakeSession(id=2, opportunity_id=1976)]

            def fake_client_factory(access_token, opportunity_id=None, organization_id=None, program_id=None):
                if program_id == 176 and opportunity_id is None:
                    return program_scoped_client
                if opportunity_id == 1973:
                    return opp_1973_client
                if opportunity_id == 1976:
                    return opp_1976_client
                raise AssertionError(f"Unexpected client construction: opp={opportunity_id} program={program_id}")

            MockAPI.side_effect = fake_client_factory

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(program_id=176, access_token="fake")
            sessions = da.get_audit_sessions()

            assert sorted(s.id for s in sessions) == [1, 2]
            program_scoped_client.get_records.assert_called_once()
            opp_1973_client.get_records.assert_called_once()
            opp_1976_client.get_records.assert_called_once()
            # The opp belonging to a DIFFERENT program was never even queried.
            assert not any(
                call.kwargs.get("opportunity_id") == 9999 or call.args == (9999,) for call in MockAPI.call_args_list
            )

    def test_dedupes_a_session_that_carries_program_id_and_also_shows_via_fanout(self):
        """A session returned by both the direct program_id query AND a
        per-opp fan-out query (shouldn't normally happen, but the API
        contract doesn't guarantee it can't) must not appear twice."""
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_get_org_data.return_value = {"opportunities": [{"id": 1973, "program": 176}]}

            program_scoped_client = MagicMock()
            program_scoped_client.get_records.return_value = [FakeSession(id=1, opportunity_id=1973)]

            opp_client = MagicMock()
            opp_client.get_records.return_value = [FakeSession(id=1, opportunity_id=1973)]

            def fake_client_factory(access_token, opportunity_id=None, organization_id=None, program_id=None):
                return program_scoped_client if opportunity_id is None else opp_client

            MockAPI.side_effect = fake_client_factory

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(program_id=176, access_token="fake")
            sessions = da.get_audit_sessions()

            assert [s.id for s in sessions] == [1]

    def test_opportunity_scoped_call_is_unaffected_no_fanout(self):
        """Single-opp scoping must behave exactly as before — straight
        pass-through, no program-hierarchy lookup at all."""
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_client = MagicMock()
            mock_client.get_records.return_value = [FakeSession(id=1, opportunity_id=1973)]
            MockAPI.return_value = mock_client

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(opportunity_id=1973, access_token="fake")
            sessions = da.get_audit_sessions()

            assert [s.id for s in sessions] == [1]
            mock_client.get_records.assert_called_once()
            mock_get_org_data.assert_not_called()

    def test_program_scope_with_no_member_opportunities_returns_direct_query_only(self):
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_get_org_data.return_value = {"opportunities": []}
            mock_client = MagicMock()
            mock_client.get_records.return_value = [FakeSession(id=5, opportunity_id=None)]
            MockAPI.return_value = mock_client

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(program_id=176, access_token="fake")
            sessions = da.get_audit_sessions()

            assert [s.id for s in sessions] == [5]


class TestGetSessionsByWorkflowRunProgramScope:
    """get_sessions_by_workflow_run() shares the same fan-out helper as
    get_audit_sessions() — a multi-opp workflow run's linked sessions are
    each individually opportunity-tagged (whichever opp was active when
    that particular session was created), so it needs the identical
    program-scoped union, not just sessions matching self.program_id."""

    def test_fans_out_and_filters_by_labs_record_id_across_program_opportunities(self):
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_get_org_data.return_value = {
                "opportunities": [
                    {"id": 1973, "program": 176},
                    {"id": 1976, "program": 176},
                ]
            }

            program_scoped_client = MagicMock()
            program_scoped_client.get_records.return_value = []

            opp_1973_client = MagicMock()
            opp_1973_client.get_records.return_value = [
                FakeSession(id=1, opportunity_id=1973, labs_record_id=42),
                FakeSession(id=2, opportunity_id=1973, labs_record_id=99),  # different run — excluded
            ]

            opp_1976_client = MagicMock()
            opp_1976_client.get_records.return_value = [
                FakeSession(id=3, opportunity_id=1976, labs_record_id=42),
            ]

            def fake_client_factory(access_token, opportunity_id=None, organization_id=None, program_id=None):
                if program_id == 176 and opportunity_id is None:
                    return program_scoped_client
                if opportunity_id == 1973:
                    return opp_1973_client
                if opportunity_id == 1976:
                    return opp_1976_client
                raise AssertionError(f"Unexpected client construction: opp={opportunity_id} program={program_id}")

            MockAPI.side_effect = fake_client_factory

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(program_id=176, access_token="fake")
            sessions = da.get_sessions_by_workflow_run(42)

            # Session 2 (run 99) is filtered out; sessions 1 and 3 span TWO
            # different opportunities and both survive — proving the fix.
            assert sorted(s.id for s in sessions) == [1, 3]

    def test_opportunity_scoped_call_is_unaffected_no_fanout(self):
        with (
            patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI,
            patch("connect_labs.labs.context.get_org_data") as mock_get_org_data,
            patch("connect_labs.workflow.data_access.settings") as mock_settings,
        ):
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            mock_client = MagicMock()
            mock_client.get_records.return_value = [
                FakeSession(id=1, opportunity_id=1973, labs_record_id=42),
                FakeSession(id=2, opportunity_id=1973, labs_record_id=99),
            ]
            MockAPI.return_value = mock_client

            from connect_labs.audit.data_access import AuditDataAccess

            da = AuditDataAccess(opportunity_id=1973, access_token="fake")
            sessions = da.get_sessions_by_workflow_run(42)

            assert [s.id for s in sessions] == [1]
            mock_get_org_data.assert_not_called()
