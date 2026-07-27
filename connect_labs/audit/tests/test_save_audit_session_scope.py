"""Regression coverage for save_audit_session()'s opportunity scoping.

Production symptom: a reviewer hits "Complete Review" on an image audit and
the review is never saved — the page reports a generic "An internal error
occurred" and the work is lost.

Root cause: get_audit_session(try_multiple_opportunities=True) deliberately
loads a session that belongs to an opportunity OTHER than the one currently
selected in the user's Django session, but save_audit_session wrote it back
through the *ambient*-scoped client without passing the record it already
held. update_record then re-fetched by id under the ambient opportunity,
found nothing, and raised "Record {id} not found" — aborting the save.

Same bug class as the workflow-side scope fix in #933, which never reached
the audit app.

These tests pin both halves of the fix: the write is scoped to the session's
own opportunity, and the already-fetched record is handed to update_record so
the redundant, mis-scoped re-fetch never happens.

Watch the storage/target distinction. AuditSessionRecord.opportunity_id is a
property over data["opportunity_id"] (the opportunity being AUDITED); the
LabsRecord's real storage scope arrives as the top-level api_data field and is
stashed in _opportunity_id_from_api. Scoping and payload-building must both
use the storage value, so these fixtures set the two independently.
"""

from unittest.mock import patch

import pytest

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.labs.integrations.connect.api_client import LabsAPIError
from connect_labs.labs.models import LocalLabsRecord

AMBIENT_OPP = 1973
SESSION_OPP = 1976


def _make_session(session_id=6840, storage_opp_id=SESSION_OPP, target_opp_id=None, status="in_progress"):
    """Build an audit session.

    storage_opp_id -> the LabsRecord scope the API filters/writes by.
    target_opp_id  -> data["opportunity_id"], the audited opportunity.
                      Defaults to matching storage, the usual case.
    """
    return AuditSessionRecord(
        {
            "id": session_id,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": storage_opp_id,
            "organization_id": None,
            "program_id": 176,
            "labs_record_id": 4392,
            "username": "reviewer@example.com",
            "data": {
                "status": status,
                "visit_ids": [],
                "visit_results": {},
                "opportunity_id": target_opp_id if target_opp_id is not None else storage_opp_id,
            },
        }
    )


class FakeAPIClient:
    """Mimics LabsRecordAPIClient.update_record's real scoping semantics.

    The behaviour that matters (api_client.py): without a current_record,
    update_record re-fetches the record by id *scoped to this client's
    opportunity_id* and raises LabsAPIError if that scoped lookup is empty.
    Records stored under another opportunity are therefore invisible. It then
    builds the write payload's opportunity_id from ``current.opportunity_id``,
    which is why handing it the wrong "current" can relocate a record.
    """

    def __init__(self, access_token, opportunity_id=None, organization_id=None, program_id=None):
        self.access_token = access_token
        self.opportunity_id = opportunity_id
        self.stored_opp_by_id = {}  # record_id -> storage opportunity_id
        self.update_calls = []
        self.refetch_calls = []
        self.closed = False

    def make_visible(self, record_id, storage_opp_id):
        self.stored_opp_by_id[record_id] = storage_opp_id

    def get_record_by_id(self, record_id, experiment=None, type=None, model_class=None):
        self.refetch_calls.append(record_id)
        storage_opp_id = self.stored_opp_by_id.get(record_id)
        if storage_opp_id is None or storage_opp_id != self.opportunity_id:
            return None
        return LocalLabsRecord(
            {
                "id": record_id,
                "experiment": "audit",
                "type": "AuditSession",
                "data": {},
                "username": "reviewer@example.com",
                "opportunity_id": storage_opp_id,
                "organization_id": None,
                "program_id": 176,
                "labs_record_id": 4392,
            }
        )

    def update_record(
        self,
        record_id,
        experiment,
        type,
        data,
        username=None,
        current_record=None,
        **kwargs,
    ):
        current = current_record or self.get_record_by_id(record_id, experiment=experiment, type=type)
        if not current:
            raise LabsAPIError(f"Record {record_id} not found")

        # The real client derives the written scope from current, not from self.
        written_opp_id = current.opportunity_id or self.opportunity_id
        self.update_calls.append(
            {
                "record_id": record_id,
                "data": data,
                "current_record": current,
                "written_opportunity_id": written_opp_id,
            }
        )
        return LocalLabsRecord(
            {
                "id": current.id,
                "experiment": current.experiment,
                "type": current.type,
                "data": data,
                "username": username or current.username,
                "opportunity_id": written_opp_id,
                "organization_id": current.organization_id,
                "program_id": current.program_id,
                "labs_record_id": current.labs_record_id,
            }
        )

    def close(self):
        self.closed = True


def _data_access(ambient_client, opportunity_id=AMBIENT_OPP):
    with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        MockAPI.return_value = ambient_client
        from connect_labs.audit.data_access import AuditDataAccess

        return AuditDataAccess(access_token="fake", opportunity_id=opportunity_id)


class TestSaveAuditSessionScope:
    def test_cross_opportunity_completion_saves(self):
        """The actual production bug: session owned by an opportunity other
        than the ambient one must still save."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        scoped_client = FakeAPIClient("fake", opportunity_id=SESSION_OPP)

        session = _make_session()
        # The record only exists under its own opportunity — the ambient
        # client genuinely cannot see it, exactly as in production.
        scoped_client.make_visible(session.id, SESSION_OPP)

        da = _data_access(ambient_client)
        session.data["status"] = "completed"
        session.data["overall_result"] = "pass"

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient", return_value=scoped_client) as MockScopedAPI:
            saved = da.save_audit_session(session)

        # A client scoped to the session's OWN opportunity did the write.
        MockScopedAPI.assert_called_once_with("fake", SESSION_OPP)
        assert len(scoped_client.update_calls) == 1
        assert not ambient_client.update_calls
        # ...and it was closed, so we don't leak an httpx client per save.
        assert scoped_client.closed

        assert saved.status == "completed"
        assert saved.overall_result == "pass"

    def test_update_record_gets_the_already_fetched_record(self):
        """current_record must be passed so update_record never does its own
        mis-scoped re-fetch."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        session = _make_session(storage_opp_id=AMBIENT_OPP)
        ambient_client.make_visible(session.id, AMBIENT_OPP)

        da = _data_access(ambient_client)
        da.save_audit_session(session)

        call = ambient_client.update_calls[0]
        assert call["current_record"] is not None
        assert call["current_record"].id == session.id
        assert ambient_client.refetch_calls == []

    def test_write_does_not_relocate_a_session_to_its_audit_target_opp(self):
        """current_record must carry the STORAGE opportunity, not the audited
        one — otherwise the save silently moves the record."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        # stored under AMBIENT_OPP, but the audit targets a different opp
        session = _make_session(storage_opp_id=AMBIENT_OPP, target_opp_id=SESSION_OPP)
        ambient_client.make_visible(session.id, AMBIENT_OPP)

        da = _data_access(ambient_client)

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient") as MockScopedAPI:
            da.save_audit_session(session)

        # scope decision followed storage, so no second client
        MockScopedAPI.assert_not_called()
        # ...and the record stays where it lives
        assert ambient_client.update_calls[0]["written_opportunity_id"] == AMBIENT_OPP

    def test_same_opportunity_does_not_build_a_second_client(self):
        """No needless client churn on the common in-scope path."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        session = _make_session(storage_opp_id=AMBIENT_OPP)
        ambient_client.make_visible(session.id, AMBIENT_OPP)

        da = _data_access(ambient_client)

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient") as MockScopedAPI:
            da.save_audit_session(session)

        MockScopedAPI.assert_not_called()
        assert len(ambient_client.update_calls) == 1

    def test_string_opportunity_id_is_not_treated_as_a_different_scope(self):
        """Scope ids arrive as str from URL params/MCP args; a str/int
        mismatch must not trigger a spurious second client."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        session = _make_session(storage_opp_id=str(AMBIENT_OPP))
        ambient_client.make_visible(session.id, str(AMBIENT_OPP))

        da = _data_access(ambient_client)

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient") as MockScopedAPI:
            da.save_audit_session(session)

        MockScopedAPI.assert_not_called()
        assert len(ambient_client.update_calls) == 1

    def test_scoped_client_closed_even_when_the_write_fails(self):
        """A failed save must not leak the per-save httpx client."""
        ambient_client = FakeAPIClient("fake", opportunity_id=AMBIENT_OPP)
        scoped_client = FakeAPIClient("fake", opportunity_id=SESSION_OPP)

        def boom(*args, **kwargs):
            raise LabsAPIError("Record 6840 not found")

        scoped_client.update_record = boom

        da = _data_access(ambient_client)

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient", return_value=scoped_client):
            with pytest.raises(LabsAPIError):
                da.save_audit_session(_make_session())

        assert scoped_client.closed
