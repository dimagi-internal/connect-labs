"""The read path must never serve an unbuilt projection as a clean history.

This is the safety property of #1246 step 2. Everything else in the switch-over
is a performance change; this one is the difference between "slow" and "tells an
auditor an image was never judged when it was".
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState, PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import is_built, rebuild_opportunity, record_session, replace_session

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(id, status, visit_results, completed_at=None, title="", opportunity_id=OPP):
    data = {
        "status": status,
        "visit_results": visit_results,
        "title": title,
        "opportunity_id": opportunity_id,
    }
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
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
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def _da():
    # __init__ demands an OAuth token; nothing here reaches the network.
    return AuditDataAccess.__new__(AuditDataAccess)


@pytest.mark.django_db
class TestReadPathFallback:
    def test_unbuilt_opportunity_falls_back_to_live(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", return_value=[s]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        spy.assert_called_once_with(status="completed")
        assert index["111:b1"]["result"] == "pass"

    def test_built_opportunity_does_not_touch_connect_at_all(self):
        """The whole point: no export-API round trip on a built opportunity."""
        s = _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        with patch.object(AuditDataAccess, "get_audit_sessions") as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        spy.assert_not_called()
        assert index["111:b1"]["result"] == "fail"

    def test_rows_without_a_state_row_are_not_trusted(self):
        """Dual-written rows accumulate before a backfill; a partial set is not a history.

        This is the case that makes `is_built` the gate rather than "are there
        any rows" -- serving a half-populated table would under-report priors.
        """
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        replace_session(s)  # writes rows, does NOT mark the opportunity built
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).exists()
        assert not is_built(OPP)

        live_only = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", return_value=[live_only]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        spy.assert_called_once()
        assert set(index) == {"222:b9"}, "must serve the live answer, not the partial table"

    def test_a_built_opportunity_with_no_verdicts_is_trusted_as_empty(self):
        """'Built and genuinely empty' must NOT fall back — otherwise nothing improves.

        The counterpart to the test above: these two states are identical in the
        rows table and only the state row tells them apart.
        """
        rebuild_opportunity(OPP, [_session(1, "in_progress", {"111": _vr(b1="pass")})])
        assert is_built(OPP)
        with patch.object(AuditDataAccess, "get_audit_sessions") as spy:
            assert _da().get_prior_audited_images(opportunity_id=OPP) == {}
        spy.assert_not_called()

    def test_exclude_session_id_is_honoured_on_the_projection_path(self):
        s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        assert _da().get_prior_audited_images(opportunity_id=OPP, exclude_session_id=7) == {}

    def test_other_opportunities_are_unaffected_by_a_build(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        assert not is_built(9999)


@pytest.mark.django_db
class TestBuildState:
    def test_rebuild_records_who_built_it_and_from_how_much(self):
        sessions = [
            _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)),
            _session(2, "in_progress", {"111": _vr(b3="pass")}),
        ]
        rebuild_opportunity(OPP, sessions, built_by="poller@example.com")
        state = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
        assert state.built_by == "poller@example.com"
        assert state.source_sessions == 2, "counts what it was handed, so a scope drop is visible"
        assert state.rows == 2


@pytest.mark.django_db
class TestRecordSessionIsBestEffort:
    def test_a_projection_failure_never_breaks_the_caller(self):
        """The audit was already saved; a cache write must not turn that into a 500."""
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch(
            "connect_labs.audit.prior_audit_projection.replace_session",
            side_effect=RuntimeError("db gone"),
        ):
            assert record_session(s) is None

    def test_a_successful_write_reports_its_row_count(self):
        s = _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1))
        assert record_session(s) == 2
