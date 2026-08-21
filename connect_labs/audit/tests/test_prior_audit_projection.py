"""The projection must agree with build_prior_audit_index, including on retraction.

These are not "does the ORM work" tests. Each one pins a rule that, if the
projection got it wrong, would show an auditor a WRONG prior verdict -- or a
clean history where there was one -- with the page rendering perfectly either
way (#1246).
"""

from datetime import datetime, timezone

import pytest

from connect_labs.audit.data_access import build_prior_audit_index
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import (
    read_index,
    rebuild_opportunity,
    replace_session,
    rows_for_session,
    verify_opportunity,
)

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(id, status, visit_results, completed_at=None, title="", opportunity_id=OPP):
    """Same shape as test_prior_audit_index._session.

    opportunity_id goes in both places: the envelope key is the base class's
    storage field, while AuditSessionRecord.opportunity_id is a property reading
    data["opportunity_id"].
    """
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


class TestRowsForSession:
    def test_only_completed_sessions_contribute(self):
        assert rows_for_session(_session(1, "in_progress", {"111": _vr(b1="pass")})) == []

    def test_only_real_verdicts_count(self):
        """A pending or blank assessment is not a prior audit."""
        s = _session(1, "completed", {"111": _vr(b1=None, b2="", b3="pass")}, completed_at=_dt(1))
        assert [r.blob_id for r in rows_for_session(s)] == ["b3"]

    def test_carries_scope_and_provenance(self):
        s = _session(7, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1), title="Week 3")
        (row,) = rows_for_session(s)
        assert (row.opportunity_id, row.session_id, row.visit_id, row.blob_id) == (OPP, 7, "111", "b1")
        assert (row.result, row.session_title) == ("fail", "Week 3")


@pytest.mark.django_db
class TestProjectionMatchesLiveIndex:
    def test_agrees_on_a_simple_history(self):
        sessions = [
            _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)),
            _session(2, "in_progress", {"111": _vr(b3="pass")}),
        ]
        rebuild_opportunity(OPP, sessions)
        assert verify_opportunity(OPP, build_prior_audit_index(sessions)).agrees

    def test_most_recently_completed_session_wins(self):
        old = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        new = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(9))
        rebuild_opportunity(OPP, [old, new])
        assert read_index(OPP)["111:b1"]["result"] == "fail"
        assert verify_opportunity(OPP, build_prior_audit_index([old, new])).agrees

    def test_a_null_completed_at_never_displaces_a_dated_verdict(self):
        dated = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        undated = _session(2, "completed", {"111": _vr(b1="fail")})
        rebuild_opportunity(OPP, [dated, undated])
        assert read_index(OPP)["111:b1"]["result"] == "pass"
        assert verify_opportunity(OPP, build_prior_audit_index([dated, undated])).agrees

    def test_exclude_session_id_matches_the_live_builder(self):
        s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        assert read_index(OPP, exclude_session_id=7) == {}
        live = build_prior_audit_index([s], exclude_session_id=7)
        assert verify_opportunity(OPP, live, exclude_session_id=7).agrees

    def test_rebuild_only_touches_its_own_opportunity(self):
        mine = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        theirs = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(1), opportunity_id=9999)
        rebuild_opportunity(9999, [theirs])
        rebuild_opportunity(OPP, [mine])
        assert (
            PriorAuditVerdict.objects.filter(opportunity_id=9999).count() == 1
        ), "rebuilding one opportunity must not delete another's rows"

    def test_rebuild_is_idempotent(self):
        sessions = [_session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1))]
        first = rebuild_opportunity(OPP, sessions)["rows_written"]
        again = rebuild_opportunity(OPP, sessions)["rows_written"]
        assert first == again == PriorAuditVerdict.objects.filter(opportunity_id=OPP).count()


@pytest.mark.django_db
class TestRetraction:
    """The reason this table stores one row per (session, image).

    ExperimentAuditUncompleteView can reopen a completed session, withdrawing
    every verdict it contributed. A table holding only the winning verdict per
    image cannot restore the older session's verdict, because it never recorded
    that the older session voted.
    """

    def test_reopening_a_session_restores_the_older_verdict(self):
        old = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        new = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(9))
        rebuild_opportunity(OPP, [old, new])
        assert read_index(OPP)["111:b1"]["result"] == "fail"

        reopened = _session(2, "in_progress", {"111": _vr(b1="fail")})
        replace_session(reopened)

        entry = read_index(OPP)["111:b1"]
        assert entry["result"] == "pass", "the older completed verdict must come back"
        assert entry["session_id"] == 1
        assert verify_opportunity(OPP, build_prior_audit_index([old, reopened])).agrees

    def test_reopening_the_only_session_clears_the_image(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        replace_session(_session(1, "in_progress", {"111": _vr(b1="pass")}))
        assert read_index(OPP) == {}

    def test_clearing_one_verdict_removes_only_that_row(self):
        """A shrunk contribution must not leave a phantom prior audit behind."""
        s = _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        assert set(read_index(OPP)) == {"111:b1", "111:b2"}

        cleared = _session(1, "completed", {"111": _vr(b1="pass", b2=None)}, completed_at=_dt(1))
        replace_session(cleared)
        assert set(read_index(OPP)) == {"111:b1"}


@pytest.mark.django_db
class TestVerifyDetectsDisagreement:
    """A verifier that cannot fail is worse than none — it licenses the switch-over."""

    def test_detects_a_missing_key(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        live = build_prior_audit_index([s])  # projection deliberately left empty
        result = verify_opportunity(OPP, live)
        assert not result.agrees and result.missing == ["111:b1"]

    def test_detects_an_extra_key(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        result = verify_opportunity(OPP, {})
        assert not result.agrees and result.extra == ["111:b1"]

    def test_detects_a_differing_verdict(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        live = build_prior_audit_index([_session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))])
        result = verify_opportunity(OPP, live)
        assert not result.agrees and result.mismatched[0]["key"] == "111:b1"
