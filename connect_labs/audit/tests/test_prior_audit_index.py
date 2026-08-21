"""Unit tests for the prior-audit index (Must-have #5)."""

from unittest.mock import patch

import pytest

from connect_labs.audit.data_access import AuditDataAccess, build_prior_audit_index
from connect_labs.audit.models import AuditSessionRecord


def _session(id, status, visit_results, completed_at=None, title="", opportunity_id=1973):
    """Build an AuditSessionRecord.

    ``opportunity_id`` goes in BOTH places on purpose. The envelope key is the
    base class's storage field (where the record is filed); the one the
    ``AuditSessionRecord.opportunity_id`` property actually returns is
    ``data["opportunity_id"]`` -- what the audit is ABOUT. Anything filtering on
    ``record.opportunity_id`` reads the latter, so a fixture that sets only the
    envelope key silently presents as ``opportunity_id is None``.
    """
    data = {
        "status": status,
        "visit_results": visit_results,
        "title": title,
        "opportunity_id": opportunity_id,
    }
    if completed_at:
        data["completed_at"] = completed_at
    return AuditSessionRecord(
        {
            "id": id,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": opportunity_id,
            "data": data,
        }
    )


def _vr(**assessments):
    # assessments: blob_id -> result
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def test_indexes_verdicts_from_completed_sessions():
    s = _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at="2026-05-01T00:00:00Z")
    index = build_prior_audit_index([s])
    assert index["111:b1"]["result"] == "pass"
    assert index["111:b2"]["result"] == "fail"
    assert index["111:b1"]["session_id"] == 1


def test_ignores_in_progress_sessions():
    s = _session(1, "in_progress", {"111": _vr(b1="pass")})
    assert build_prior_audit_index([s]) == {}


def test_ignores_pending_images():
    s = _session(1, "completed", {"111": _vr(b1=None, b2="")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s]) == {}


def test_duplicate_fake_counts_as_audited():
    s = _session(1, "completed", {"111": _vr(b1="duplicate_fake")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s])["111:b1"]["result"] == "duplicate_fake"


def test_excludes_named_session():
    s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s], exclude_session_id=7) == {}


def test_most_recent_verdict_wins():
    old = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
    new = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at="2026-06-01T00:00:00Z")
    index = build_prior_audit_index([old, new])
    assert index["111:b1"]["result"] == "fail"
    assert index["111:b1"]["session_id"] == 2


@pytest.mark.django_db
class TestPriorAuditIndexFetchesOnlyCompleted:
    """The index must not drag in-progress sessions over the wire to discard them.

    An AuditSessionRecord is not a local row -- it is fetched from Connect's
    export API carrying its whole `data` blob (every visit_images and
    visit_results entry). get_prior_audited_images runs on every
    /audit/api/<id>/bulk-data/ load, whose own response is single-digit
    kilobytes, so filtering in Python after the fetch pays the download and JSON
    parse of every in-progress session in scope for nothing (#1246).

    These pin the REQUEST, not just the result, because a regression here is
    silent: the page still renders correctly, just slowly, which is exactly how
    it went unnoticed until the tier saturated.

    django_db because get_prior_audited_images now checks whether this
    opportunity has a BUILT projection before deciding to compute live. Nothing
    here builds one, so every test in this class exercises the FALLBACK path --
    which is the one that still talks to Connect, and therefore the one these
    assertions are about.
    """

    @staticmethod
    def _da():
        # Bypass __init__: it demands an OAuth token, and none of these tests
        # reach the network -- get_audit_sessions is patched in every one.
        return AuditDataAccess.__new__(AuditDataAccess)

    def test_asks_the_api_for_completed_sessions_only(self):
        with patch.object(AuditDataAccess, "get_audit_sessions", return_value=[]) as spy:
            self._da().get_prior_audited_images(opportunity_id=1973)
        spy.assert_called_once_with(status="completed")

    def test_still_scopes_to_the_requested_opportunity(self):
        mine = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
        other = _session(
            2,
            "completed",
            {"222": _vr(b9="fail")},
            completed_at="2026-05-02T00:00:00Z",
            opportunity_id=9999,
        )
        with patch.object(AuditDataAccess, "get_audit_sessions", return_value=[mine, other]):
            index = self._da().get_prior_audited_images(opportunity_id=1973)
        assert "111:b1" in index
        assert "222:b9" not in index, "another opportunity's verdicts must not leak in"

    def test_exclude_session_id_is_still_honoured(self):
        s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
        with patch.object(AuditDataAccess, "get_audit_sessions", return_value=[s]):
            index = self._da().get_prior_audited_images(opportunity_id=1973, exclude_session_id=7)
        assert index == {}

    def test_a_session_without_a_status_key_is_not_indexed(self):
        """Mirrors the server-side filter, which is what makes moving it safe.

        AuditSessionRecord.status reads data["status"] defaulting to
        "in_progress", so a session missing the key is dropped by the Python
        check and equally unmatched by data__status=completed. The predicate is
        the same one, moved.
        """
        bare = AuditSessionRecord(
            {
                "id": 3,
                "experiment": "audit",
                "type": "AuditSession",
                "opportunity_id": 1973,
                "data": {"visit_results": {"111": _vr(b1="pass")}},
            }
        )
        assert bare.status == "in_progress"
        assert build_prior_audit_index([bare]) == {}
