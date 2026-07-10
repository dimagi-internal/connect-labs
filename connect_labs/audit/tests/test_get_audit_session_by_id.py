"""Regression coverage for get_audit_session()'s by-id lookup (#905).

The previous implementation fetched EVERY AuditSession in scope (full
payloads) and linear-scanned for one id — and repeated that per opportunity
in the cross-opp fallback. These tests pin the fixed shape: the server-side
by-id filter (get_record_by_id) is the only read, and get_records is never
called for a by-id lookup.
"""

from unittest.mock import MagicMock, patch


class FakeSession:
    def __init__(self, id, opportunity_id):
        self.id = id
        self.opportunity_id = opportunity_id


def _data_access(main_client):
    with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        MockAPI.return_value = main_client
        from connect_labs.audit.data_access import AuditDataAccess

        return AuditDataAccess(access_token="fake", opportunity_id=1973)


class TestGetAuditSessionById:
    def test_current_scope_uses_by_id_lookup_not_fetch_all(self):
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = FakeSession(id=7, opportunity_id=1973)

        da = _data_access(main_client)
        session = da.get_audit_session(7)

        assert session.id == 7
        main_client.get_record_by_id.assert_called_once_with(
            7,
            experiment="audit",
            type="AuditSession",
            model_class=__import__("connect_labs.audit.models", fromlist=["AuditSessionRecord"]).AuditSessionRecord,
        )
        main_client.get_records.assert_not_called()

    def test_miss_without_cross_opp_returns_none(self):
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = None

        da = _data_access(main_client)
        assert da.get_audit_session(404) is None
        main_client.get_records.assert_not_called()

    def test_cross_opp_fallback_uses_by_id_per_opportunity(self):
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = None

        found = FakeSession(id=9, opportunity_id=1976)
        temp_client = MagicMock()
        temp_client.get_record_by_id.return_value = found

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1973}, {"id": 1976}])

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient", return_value=temp_client) as MockTempAPI:
            session = da.get_audit_session(9, try_multiple_opportunities=True)

        assert session is found
        # current opp (1973) is skipped; only 1976 gets a temp client
        MockTempAPI.assert_called_once_with("fake", 1976)
        temp_client.get_record_by_id.assert_called_once()
        temp_client.get_records.assert_not_called()
        temp_client.close.assert_called_once()
