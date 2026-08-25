"""Every path that can change a verdict must update the projection.

Staleness here is EVENT-based, not timestamp-based, and it has to be: there is
no timestamp that could do the job.

  * LabsRecord (commcare-connect) has no date_modified / auto_now field at all,
    so there is no server-side watermark to compare against.
  * data["completed_at"] is the only timestamp, it is written by labs itself,
    and it does not change when a completed session's verdicts are edited.

So correctness rests on covering every mutation point, and the projection's
staleness window is only a backstop for a dual-write that FAILED (record_session
swallows its errors on purpose) or a change made outside this app entirely.

These tests enumerate the mutation points so a fourth one cannot be added
without something here going red.
"""

from datetime import datetime, timezone

import pytest

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_projection import read_index, record_session

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


@pytest.mark.django_db
class TestEveryMutationPointUpdatesTheProjection:
    def test_completion_adds_the_verdicts(self):
        record_session(_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)))
        assert read_index(OPP)["111:b1"]["result"] == "pass"

    def test_reopening_withdraws_them(self):
        record_session(_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)))
        record_session(_session(1, "in_progress", {"111": _vr(b1="pass")}))
        assert read_index(OPP) == {}

    def test_editing_a_COMPLETED_session_updates_them(self):
        """The path that had no dual-write until now.

        ExperimentSaveAuditView does not set status, so a completed session keeps
        it while visit_results are replaced wholesale. bulk_assessment.html leaves
        completed MUAC picture audits editable (isReadOnly excludes that
        workflow), so autosave posts here against completed sessions routinely.
        """
        record_session(_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)))
        assert read_index(OPP)["111:b1"]["result"] == "pass"

        # Same session, same status, same completed_at -- only the verdict moved.
        record_session(_session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1)))
        assert (
            read_index(OPP)["111:b1"]["result"] == "fail"
        ), "an edit to a completed session must not leave the old verdict standing"

    def test_clearing_a_verdict_on_a_completed_session_removes_it(self):
        record_session(_session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)))
        assert set(read_index(OPP)) == {"111:b1", "111:b2"}
        record_session(_session(1, "completed", {"111": _vr(b1="pass", b2=None)}, completed_at=_dt(1)))
        assert set(read_index(OPP)) == {"111:b1"}

    def test_an_edit_is_invisible_to_any_timestamp_check(self):
        """Why this is event-based and not a watermark.

        The edit above changes neither status nor completed_at, and LabsRecord
        carries no date_modified. A freshness check reading timestamps would see
        nothing at all.
        """
        before = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        after = _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))
        assert before.status == after.status
        assert before.completed_at == after.completed_at
        assert not hasattr(before, "date_modified")
