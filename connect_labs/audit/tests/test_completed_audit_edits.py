"""A completed audit is only editable where that is deliberate, and says so.

Two properties, from two different failures:

  * The server must enforce the rule the browser was enforcing alone. Read-only
    lived in bulk_assessment.html's isReadOnly; the save endpoint never checked
    status, so a stale tab, a replayed POST or a future client change could
    rewrite a completed audit's verdicts and the server would accept it.

  * An edit re-dates the audit. Its conclusions ARE its content, so one whose
    verdicts changed today was not meaningfully completed on the original date,
    and completed_at moves with the edit.

A separate last_edited_at was tried first and removed. Two timestamps meant the
live builder ordered verdicts on completed_at while the projection ordered on
the other one, so for an image judged by two completed sessions where one was
later edited, THE TWO PATHS PICKED DIFFERENT WINNERS -- a permanent
disagreement in verify_opportunity. The original completion time is already
preserved in the HIPAA audit trail (update_record is @_audited(UPDATE)), with
the whole sequence rather than just the first value, so the second field bought
nothing and cost correctness.
"""

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

    Patched at the predicate rather than driven through a real workflow lookup:
    _is_muac_picture_audit_session costs an API round trip, and what these pin is
    the branch, not that lookup.
    """

    def _post(self, session, is_muac):
        req = RequestFactory().post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})
        req.user = type("U", (), {"is_authenticated": True, "username": "someone"})()
        with (
            patch.object(views.AuditDataAccess, "__init__", return_value=None),
            patch.object(views.AuditDataAccess, "close", return_value=None),
            patch.object(views.AuditDataAccess, "get_audit_session", return_value=session),
            patch.object(views.AuditDataAccess, "save_audit_session", side_effect=lambda s: s),
            patch.object(views, "_is_muac_picture_audit_session", return_value=is_muac),
            patch.object(views, "sync_after_save", return_value=None),
            patch("connect_labs.audit.prior_audit_projection.record_session", return_value=0),
        ):
            return views.ExperimentSaveAuditView().post(req, session.id)

    def test_a_completed_non_muac_audit_is_refused(self):
        resp = self._post(_session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)), is_muac=False)
        assert resp.status_code == 409

    def test_a_completed_muac_audit_is_still_editable(self):
        """The deliberate carve-out isReadOnly draws -- it must survive."""
        resp = self._post(_session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)), is_muac=True)
        assert resp.status_code == 200

    def test_an_in_progress_audit_is_unaffected(self):
        resp = self._post(_session("in_progress", {"111": _vr(b1="pass")}), is_muac=False)
        assert resp.status_code == 200

    def test_a_lookup_failure_on_a_completed_audit_refuses_rather_than_allows(self):
        """_is_muac_picture_audit_session fails closed; the guard inherits that.

        A transient API blip must not become permission to edit a completed
        audit. Autosave keeps hasUnsavedChanges set and retries, so refusing
        loses nothing.
        """
        resp = self._post(_session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)), is_muac=False)
        assert resp.status_code == 409

    def test_editing_a_completed_muac_audit_moves_its_completion_date(self):
        """The edit path actually stamps it -- not just the model doing so."""
        s = _session("completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        before = s.data["completed_at"]
        self._post(s, is_muac=True)
        assert s.data["completed_at"] != before

    def test_an_in_progress_save_does_not_invent_a_completion_date(self):
        s = _session("in_progress", {"111": _vr(b1="pass")})
        self._post(s, is_muac=False)
        assert "completed_at" not in s.data
