"""A completed audit is only editable where that is deliberate, and says so.

Two properties, from two different failures:

  * The server must enforce the rule the browser was enforcing alone. Read-only
    lived in bulk_assessment.html's isReadOnly; the save endpoint never checked
    status, so a stale tab, a replayed POST or a future client change could
    rewrite a completed audit's verdicts and the server would accept it.

  * A completed audit is dated by the verdicts it carries. Its conclusions ARE
    its content, so one whose verdicts changed today was not meaningfully
    completed on the original date. Since #1385 that is upheld by refusing the
    edit outright rather than by re-dating it: the save endpoint 409s on any
    completed session, and the only route to a change is Reopen, which clears
    completed_at and drops the session to in_progress so re-completing stamps a
    current date.

A separate last_edited_at was tried first and removed. Two timestamps meant the
live builder ordered verdicts on completed_at while the projection ordered on
the other one, so for an image judged by two completed sessions where one was
later edited, THE TWO PATHS PICKED DIFFERENT WINNERS -- a permanent
disagreement in verify_opportunity. The original completion time is already
preserved in the HIPAA audit trail (update_record is @_audited(UPDATE)), with
the whole sequence rather than just the first value, so the second field bought
nothing and cost correctness.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from connect_labs.audit import views
from connect_labs.audit.data_access import build_prior_audit_index
from connect_labs.audit.models import AuditSessionRecord

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(status, visit_results, completed_at=None, id=1):
    data = {"status": status, "visit_results": visit_results, "title": "", "opportunity_id": OPP}
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": OPP, "data": data}
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


class TestVerdictsAreDatedHonestly:
    def test_an_edited_session_is_dated_by_the_edit(self):
        """The index feeds prior_session_date, rendered as "Audited ... on <date>"."""
        s = _session("completed", {"111": _vr(b1="fail")}, completed_at=_dt(9))
        assert build_prior_audit_index([s])["111:b1"]["completed_at"].startswith("2026-05-09")

    def test_an_unedited_session_is_dated_by_its_completion(self):
        s = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        assert build_prior_audit_index([s])["111:b1"]["completed_at"].startswith("2026-05-01")

    def test_both_index_paths_agree_after_an_edit(self):
        """The regression a second timestamp caused.

        A judged day 1 then edited (completed_at now day 9); B completed day 5,
        untouched. With two fields the live builder ordered on the frozen value
        and picked B while the projection ordered on the edit date and picked A.
        One field cannot diverge.
        """
        edited = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(9), id=1)
        untouched = _session("completed", {"111": _vr(b1="fail")}, completed_at=_dt(5), id=2)

        live = build_prior_audit_index([edited, untouched])["111:b1"]
        projected_winner = sorted(
            [edited, untouched],
            key=lambda x: (x.completed_at is not None, x.completed_at, x.id),
        )[-1]
        assert (
            live["session_id"] == projected_winner.id == 1
        ), "the more recently dated verdict must win, identically in both paths"

    def test_the_record_carries_no_second_timestamp(self):
        """Guards against reintroducing the divergence."""
        s = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        assert not hasattr(s, "verdicts_dated_at")
        assert "last_edited_at" not in s.data


@pytest.mark.django_db
class TestTheSaveEndpointRefusesCompletedAudits:
    """Enforced on the server, not just in the browser.

    This guard used to carry exactly one exception -- the "Muac Picture Audit"
    workflow, whose completed reports stayed editable on purpose, resolved by a
    per-request workflow-run lookup. #1385 withdrew that product decision, so the
    rule is now unconditional and there is no predicate left to patch. The tests
    below pin the ABSENCE of a carve-out, which is the property that regressed
    most cheaply: reintroducing one is a two-line change.
    """

    def _post(self, session):
        req = RequestFactory().post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})
        req.user = type("U", (), {"is_authenticated": True, "username": "someone"})()
        with (
            patch.object(views.AuditDataAccess, "__init__", return_value=None),
            patch.object(views.AuditDataAccess, "close", return_value=None),
            patch.object(views.AuditDataAccess, "get_audit_session", return_value=session),
            patch.object(views.AuditDataAccess, "save_audit_session", side_effect=lambda s: s),
            patch.object(views, "sync_after_save", return_value=None),
            patch("connect_labs.audit.prior_audit_projection.record_session", return_value=0),
        ):
            return views.ExperimentSaveAuditView().post(req, session.id)

    def test_a_completed_audit_is_refused(self):
        resp = self._post(_session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)))
        assert resp.status_code == 409

    def test_an_in_progress_audit_is_unaffected(self):
        resp = self._post(_session("in_progress", {"111": _vr(b1="pass")}))
        assert resp.status_code == 200

    def test_no_workflow_gets_an_exception(self):
        """The carve-out is gone -- a session from ANY workflow run is refused.

        The removed predicate keyed off session.workflow_run_id, so a session
        that carries one is the shape that used to be able to slip through.
        """
        s = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        s.data["workflow_run_id"] = 7117
        assert self._post(s).status_code == 409

    def test_a_refused_save_changes_nothing(self):
        """It refuses BEFORE writing -- verdicts and the completion date both stand.

        The endpoint used to re-date a completed session it was about to edit.
        Nothing is edited now, so nothing is re-dated, and a replayed POST cannot
        move a completed audit's date without changing its content.
        """
        s = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        before_results = json.dumps(s.data["visit_results"], sort_keys=True)
        before_completed_at = s.data["completed_at"]

        assert self._post(s).status_code == 409

        assert json.dumps(s.data["visit_results"], sort_keys=True) == before_results
        assert s.data["completed_at"] == before_completed_at

    def test_an_in_progress_save_does_not_invent_a_completion_date(self):
        s = _session("in_progress", {"111": _vr(b1="pass")})
        self._post(s)
        assert "completed_at" not in s.data
