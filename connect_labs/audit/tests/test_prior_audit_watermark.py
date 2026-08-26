"""Freshness by watermark: no work when idle, incremental when something moved.

The earlier design rebuilt on a 15-minute TTL, so a reader past the window paid
a full fetch whether or not anything had changed, and a change inside the window
went unseen for up to 15 minutes. Asking the export API for sessions later than
a stored watermark is both cheaper and fresher -- and the records it returns are
exactly the ones to merge, so the check and the update are one round trip.

Measured on production opp 2157 (339 completed sessions): the watermark query
costs 0.11s against 1.79s for a full fetch.

Only possible because completed_at MOVES when a completed session is edited
(#1286); while it was frozen there was no timestamp tracking a verdict change.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.utils import timezone as dj_timezone

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState
from connect_labs.audit.prior_audit_projection import read_index, rebuild_opportunity

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(id, status, visit_results, completed_at=None, opportunity_id=OPP):
    data = {"status": status, "visit_results": visit_results, "title": "", "opportunity_id": opportunity_id}
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": opportunity_id, "data": data}
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def _da():
    return AuditDataAccess.__new__(AuditDataAccess)


@pytest.mark.django_db
class TestWatermarkFreshness:
    def test_a_full_build_records_the_high_mark(self):
        rebuild_opportunity(
            OPP,
            [
                _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)),
                _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(5)),
            ],
        )
        assert PriorAuditProjectionState.objects.get(opportunity_id=OPP).watermark == _dt(5)

    def test_an_idle_opportunity_does_no_work(self):
        """The point: nothing changed, so nothing is rebuilt."""
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        # Asked only the cheap question, and only for records past the watermark.
        assert spy.call_count == 1
        assert spy.call_args.kwargs["completed_at__gt"] == _dt(1).isoformat()
        assert index["111:b1"]["result"] == "pass"

    def test_a_changed_session_is_merged_from_that_same_query(self):
        """No second round trip: the staleness check already returned the work."""
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        newer = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(9))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[newer]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert spy.call_count == 1
        assert set(index) == {"111:b1", "222:b9"}
        assert PriorAuditProjectionState.objects.get(opportunity_id=OPP).watermark == _dt(9)

    def test_an_edit_is_picked_up_on_the_very_next_read(self):
        """No staleness window -- the TTL version could miss this for 15 minutes."""
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        edited = _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(9))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[edited]):
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert index["111:b1"]["result"] == "fail"

    def test_the_watermark_never_rewinds(self):
        """Otherwise the same records are re-fetched forever."""
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(9))])
        projection.merge_changed(OPP, [_session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(3))])
        assert PriorAuditProjectionState.objects.get(opportunity_id=OPP).watermark == _dt(9)

    def test_past_the_backstop_it_rebuilds_fully(self):
        """The watermark cannot see a REMOVAL, so a full floor remains."""
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        st = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
        PriorAuditProjectionState.objects.filter(pk=st.pk).update(
            built_at=dj_timezone.now() - (projection.STALE_AFTER + timedelta(hours=1))
        )
        survivor = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[survivor]) as spy:
            _da().get_prior_audited_images(opportunity_id=OPP)
        # Full path: asked for ALL completed sessions, not just later ones.
        assert "completed_at__gt" not in spy.call_args.kwargs

    def test_an_unbuilt_opportunity_still_takes_the_cold_path(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert "completed_at__gt" not in spy.call_args.kwargs
        assert index["111:b1"]["result"] == "pass"

    def test_merging_a_reopened_session_withdraws_its_verdicts(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        projection.merge_changed(OPP, [_session(1, "in_progress", {"111": _vr(b1="pass")})])
        assert read_index(OPP) == {}


class TestTheWatermarkKwargIsActuallyAccepted:
    """The real signature, not a mock's.

    The watermark shipped broken: get_prior_audited_images called
    get_audit_sessions(completed_at__gt=...) and the real method took only
    (username, status), so it raised TypeError in production. Every test passed,
    because they patched get_audit_sessions with a bare Mock -- which accepts
    any signature at all.

    The view catches that exception and falls back to an EMPTY prior-audit
    index, so the failure showed auditors "nothing was previously audited"
    rather than erroring. Exactly the silent wrong answer this projection exists
    to prevent.

    Every mock of that method is now autospec'd so a signature mismatch fails
    the test; this one skips mocks entirely and inspects the callable.
    """

    def test_get_audit_sessions_accepts_arbitrary_data_filters(self):
        import inspect

        sig = inspect.signature(AuditDataAccess.get_audit_sessions)
        assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()), (
            "get_audit_sessions must accept **data_filters; the watermark read path passes "
            "completed_at__gt through it"
        )

    def test_binding_the_watermark_call_does_not_raise(self):
        """What production actually did, checked against the real signature."""
        import inspect

        inspect.signature(AuditDataAccess.get_audit_sessions).bind(
            None, status="completed", completed_at__gt="2026-05-01T00:00:00+00:00"
        )
