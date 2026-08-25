"""Reconciliation must close the drift loop without becoming a way to lose data.

record_session swallows its errors on purpose, so drift is expected and this is
what finds it. The hazard is the repair: rebuilding from a narrower identity's
view of Connect deletes prior verdicts that exist, silently, on a schedule.
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
from connect_labs.audit.prior_audit_projection import rebuild_opportunity

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(id, status, visit_results, completed_at=None, opportunity_id=OPP):
    data = {
        "status": status,
        "visit_results": visit_results,
        "title": "",
        "opportunity_id": opportunity_id,
    }
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": opportunity_id, "data": data}
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


@pytest.fixture
def poller(django_user_model):
    return django_user_model.objects.create(username="poller")


def _run(sessions, expect_drift=False, **opts):
    """Run the command with Connect stubbed to return `sessions`.

    ``expect_drift`` swallows the CommandError so a test can assert on the
    OUTPUT of a run that also, correctly, exits non-zero.
    """
    out = StringIO()
    with (
        patch(
            "connect_labs.audit.management.commands.reconcile_prior_audit_index.get_valid_access_token",
            return_value="tok",
        ),
        patch.object(AuditDataAccess, "__init__", return_value=None),
        patch.object(AuditDataAccess, "close", return_value=None),
        patch.object(AuditDataAccess, "get_audit_sessions", return_value=sessions),
    ):
        try:
            call_command("reconcile_prior_audit_index", stdout=out, stderr=out, **opts)
        except CommandError:
            if not expect_drift:
                raise
    return out.getvalue()


@pytest.mark.django_db
class TestReconcile:
    def test_reports_ok_when_the_projection_matches(self, poller):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s], built_by="poller")
        assert "ok" in _run([s], username="poller")

    def test_drift_exits_non_zero_so_it_can_gate(self, poller):
        rebuild_opportunity(OPP, [], built_by="poller")
        newer = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(2))
        with pytest.raises(CommandError, match="drifted"):
            _run([newer], username="poller")

    def test_repair_rebuilds_the_drifted_opportunity(self, poller):
        rebuild_opportunity(OPP, [], built_by="poller")
        newer = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(2))
        _run([newer], username="poller", repair=True)
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP, blob_id="b1").exists()

    def test_repairing_from_a_narrower_scope_cannot_delete_verdicts(self, poller):
        """The case that used to need a veto, and now needs none.

        rebuild_opportunity merges: it refreshes rows for sessions it SAW and
        leaves the rest alone, so a narrow identity repairing an opportunity can
        no longer destroy verdicts it was never able to read. The earlier version
        of this test asserted that the command REFUSED here -- a guard that only
        held because someone remembered to write it, and that did not protect the
        first build or the rebuild command at all. The property is now in the
        write path itself.
        """
        a = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        b = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        rebuild_opportunity(OPP, [a, b], built_by="wide-scope-user")
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 2

        _run([a], username="poller", repair=True)  # b is invisible to this identity

        assert (
            PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 2
        ), "a narrowed identity must not be able to delete verdicts"
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP, blob_id="b9").exists()

    def test_a_narrow_identity_reports_agreement_rather_than_phantom_drift(self, poller):
        """Rows it cannot see are beyond its scope, not evidence of drift.

        Reported as drift, this would fire on every run for any narrow identity
        and train people to ignore the one signal that catches real problems.
        """
        a = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        b = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        rebuild_opportunity(OPP, [a, b], built_by="wide-scope-user")

        out = _run([a], username="poller")  # no CommandError => no drift claimed

        assert "ok" in out, "no drift should be claimed"
        assert "beyond-scope=1" in out, "the rows it cannot see must be named, not silently ignored"
        assert "[SCOPE]" in out, "and the narrower view itself is worth flagging"

    def test_unknown_user_is_an_error_not_an_empty_pass(self):
        with pytest.raises(CommandError, match="does not exist"):
            call_command("reconcile_prior_audit_index", username="nobody")

    def test_unbuilt_opportunities_are_not_reconciled(self, poller):
        """Nothing to repair: they already fall back to live computation."""
        assert not PriorAuditProjectionState.objects.exists()
        out = _run([], username="poller")
        assert "no built projections" in out
