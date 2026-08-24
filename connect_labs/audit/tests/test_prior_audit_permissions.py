"""No permission level may cause the projection to lose information.

The source is Connect's export API, which returns only what the CALLING
identity's org membership can see. Every write path therefore has to be safe
under an identity that sees LESS than the last one did -- otherwise a
narrow-scoped rebuild silently deletes verdicts it was never able to read, and
an auditor is told an image was never judged when it was.

The property these tests pin is monotonicity: identities with different scopes
contribute different subsets and the table converges to their union. A narrower
caller can fail to ADD; it can never SUBTRACT.
"""

from datetime import datetime, timezone

import pytest

from connect_labs.audit.data_access import build_prior_audit_index
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState, PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import (
    read_index,
    rebuild_opportunity,
    replace_session,
    verify_opportunity,
)

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


# A wide identity sees both; a narrow one sees only the first.
WIDE = [
    _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)),
    _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2)),
]
NARROW = [WIDE[0]]


@pytest.mark.django_db
class TestNoPermissionLevelLosesInformation:
    def test_a_narrow_rebuild_after_a_wide_one_keeps_everything(self):
        """The regression that matters: this used to delete 222:b9 outright."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        assert set(read_index(OPP)) == {"111:b1", "222:b9"}

        rebuild_opportunity(OPP, NARROW, built_by="narrow")

        assert set(read_index(OPP)) == {
            "111:b1",
            "222:b9",
        }, "a caller that cannot SEE a session must not be able to DELETE its verdicts"

    def test_repeated_narrow_rebuilds_never_erode_the_union(self):
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        for _ in range(3):
            rebuild_opportunity(OPP, NARROW, built_by="narrow")
        assert set(read_index(OPP)) == {"111:b1", "222:b9"}

    def test_order_of_identities_does_not_change_the_result(self):
        """Union, not last-writer-wins."""
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        narrow_first = set(read_index(OPP))

        PriorAuditVerdict.objects.all().delete()
        PriorAuditProjectionState.objects.all().delete()

        rebuild_opportunity(OPP, WIDE, built_by="wide")
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        assert set(read_index(OPP)) == narrow_first == {"111:b1", "222:b9"}

    def test_a_narrow_identity_still_contributes_what_it_can_see(self):
        """Monotonic means it can still ADD -- it just cannot take away."""
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        assert set(read_index(OPP)) == {"111:b1"}
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        assert set(read_index(OPP)) == {"111:b1", "222:b9"}

    def test_state_never_records_a_smaller_scope_than_it_holds(self):
        """Otherwise the projection looks built from less than it actually has."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        state = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
        assert state.source_sessions == 2
        assert state.built_by == "wide"
        assert state.rows == 2, "rows must count the whole table, not this identity's slice"


@pytest.mark.django_db
class TestRetractionStillWorksUnderMergeSemantics:
    """Merge must not cost us the ability to withdraw a verdict."""

    def test_a_seen_session_that_reopened_loses_its_rows(self):
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        reopened = _session(2, "in_progress", {"222": _vr(b9="fail")})
        rebuild_opportunity(OPP, [WIDE[0], reopened], built_by="wide")
        assert set(read_index(OPP)) == {"111:b1"}, "a retraction we can SEE must still apply"

    def test_replace_session_is_unaffected_by_scope(self):
        """The dual-write path writes the session in hand and needs no visibility."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        replace_session(_session(2, "in_progress", {"222": _vr(b9="fail")}))
        assert set(read_index(OPP)) == {"111:b1"}


@pytest.mark.django_db
class TestPruningIsOptInOnly:
    def test_unseen_rows_survive_by_default(self):
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        assert "222:b9" in read_index(OPP)

    def test_prune_unseen_removes_them_when_explicitly_asked(self):
        """The escape hatch for a session deleted upstream -- and the one
        operation a narrow identity could use to lose data, hence opt-in."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        result = rebuild_opportunity(OPP, NARROW, built_by="wide-again", prune_unseen=True)
        assert result["rows_pruned"] == 1
        assert set(read_index(OPP)) == {"111:b1"}


@pytest.mark.django_db
class TestVerifierIsHonestAboutScope:
    def test_rows_beyond_this_identitys_scope_are_not_called_drift(self):
        """Otherwise the verifier fires forever for any narrow identity, and a
        verifier people learn to ignore is the same as no verifier."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        live = build_prior_audit_index(NARROW)
        result = verify_opportunity(OPP, live, visible_session_ids={NARROW[0].id})
        assert result.agrees
        assert result.beyond_scope == 1

    def test_without_a_visible_set_every_row_is_judged(self):
        """Correct only for an identity known to see everything."""
        rebuild_opportunity(OPP, WIDE, built_by="wide")
        result = verify_opportunity(OPP, build_prior_audit_index(NARROW))
        assert not result.agrees
        assert result.extra == ["222:b9"]

    def test_real_drift_inside_scope_is_still_caught(self):
        """Scope-awareness must not become a blanket excuse."""
        rebuild_opportunity(OPP, NARROW, built_by="narrow")
        changed = _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))
        result = verify_opportunity(OPP, build_prior_audit_index([changed]), visible_session_ids={1})
        assert not result.agrees
        assert result.mismatched[0]["key"] == "111:b1"
