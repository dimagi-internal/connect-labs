"""A build that fails its own gate must leave nothing behind.

rebuild_opportunity writes the PriorAuditProjectionState row, and that row is
what flips is_built() -- so the instant it commits, every auditor on that
opportunity is served the projection instead of the live computation. Verifying
afterwards meant the command could print "do NOT switch the read path" about a
switch it had already made.

It matters more here than for a typical cache: a wrong projection does not
raise, it renders as "this image was never audited".
"""

from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState, PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import is_built

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


GOOD = [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))]


def _run(sessions, expect_error=False, **opts):
    out = StringIO()
    with (
        patch.object(AuditDataAccess, "__init__", return_value=None),
        patch.object(AuditDataAccess, "close", return_value=None),
        patch.object(AuditDataAccess, "get_audit_sessions", return_value=sessions),
    ):
        try:
            call_command("rebuild_prior_audit_index", opportunity=OPP, token="t", stdout=out, stderr=out, **opts)
        except CommandError:
            if not expect_error:
                raise
    return out.getvalue()


@pytest.mark.django_db
class TestTheGatePrecedesTheCommit:
    def test_an_agreeing_build_commits_and_marks_the_opportunity_built(self):
        out = _run(GOOD)
        assert is_built(OPP)
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 1
        assert "COMMITTED" in out

    def test_a_disagreeing_build_leaves_NOTHING_behind(self):
        """The regression: this used to commit, then advise against committing."""
        # verify_opportunity is what decides; force it to disagree.
        with patch("connect_labs.audit.prior_audit_projection.verify_opportunity") as v:
            v.return_value.agrees = False
            v.return_value.summary.return_value = "forced disagreement"
            v.return_value.live_keys = 1
            v.return_value.projected_keys = 0
            v.return_value.beyond_scope = 0
            v.return_value.missing = ["111:b1"]
            v.return_value.extra = []
            v.return_value.mismatched = []
            out = _run(GOOD, expect_error=True)

        assert not is_built(OPP), "a failed gate must not leave the opportunity built"
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 0
        assert "ROLLED BACK" in out

    def test_a_disagreeing_build_still_exits_non_zero(self):
        with patch("connect_labs.audit.prior_audit_projection.verify_opportunity") as v:
            v.return_value.agrees = False
            v.return_value.summary.return_value = "forced"
            v.return_value.live_keys = v.return_value.projected_keys = 0
            v.return_value.beyond_scope = 0
            v.return_value.missing = v.return_value.extra = v.return_value.mismatched = []
            with pytest.raises(CommandError, match="nothing was committed"):
                _run(GOOD)


@pytest.mark.django_db
class TestVerifyOnlyIsARealDryRun:
    def test_it_rolls_back_even_when_the_build_would_agree(self):
        out = _run(GOOD, verify_only=True)
        assert not is_built(OPP), "--verify-only must never leave the opportunity built"
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 0
        assert "DRY RUN" in out
        assert "would agree" in out

    def test_it_answers_the_question_a_bare_diff_could_not(self):
        """Before, --verify-only diffed an EMPTY table against live and called
        every key missing, which told you nothing about whether a build works."""
        out = _run(GOOD, verify_only=True)
        assert "AGREES" in out
        assert "missing=0" in out

    def test_a_dry_run_that_would_fail_says_so_and_writes_nothing(self):
        with patch("connect_labs.audit.prior_audit_projection.verify_opportunity") as v:
            v.return_value.agrees = False
            v.return_value.summary.return_value = "forced"
            v.return_value.live_keys = v.return_value.projected_keys = 0
            v.return_value.beyond_scope = 0
            v.return_value.missing = v.return_value.extra = v.return_value.mismatched = []
            out = _run(GOOD, verify_only=True, expect_error=True)
        assert "would NOT agree" in out
        assert PriorAuditProjectionState.objects.count() == 0
