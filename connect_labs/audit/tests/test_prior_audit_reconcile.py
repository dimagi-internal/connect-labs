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
        patch("connect_labs.audit.management.commands.reconcile_prior_audit_index.get_valid_access_token",
              return_value="tok"),
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

    def test_refuses_to_repair_from_a_narrower_scope(self, poller):
        """The dangerous case: fewer sessions visible than the build saw.

        Rebuilding here would DELETE prior verdicts that exist. Pulse hit the
        same trap from the other side and understated everything ~5x silently.
        """
        a = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        b = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        rebuild_opportunity(OPP, [a, b], built_by="wide-scope-user")
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 2

        # Refusing still leaves the opportunity drifted, so the run also exits
        # non-zero -- refusing to repair must not read as "everything is fine".
        out = _run([a], username="poller", repair=True, expect_drift=True)  # b is invisible now

        assert "REFUSING to repair" in out
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 2, (
            "a narrowed identity must not be able to delete verdicts"
        )

    def test_unknown_user_is_an_error_not_an_empty_pass(self):
        with pytest.raises(CommandError, match="does not exist"):
            call_command("reconcile_prior_audit_index", username="nobody")

    def test_unbuilt_opportunities_are_not_reconciled(self, poller):
        """Nothing to repair: they already fall back to live computation."""
        assert not PriorAuditProjectionState.objects.exists()
        out = _run([], username="poller")
        assert "no built projections" in out


@pytest.mark.django_db
class TestScheduledTask:
    def test_skips_quietly_when_no_identity_is_configured(self, settings):
        """An unset identity is a configuration state, not a pageable failure."""
        from connect_labs.audit.prior_audit_tasks import reconcile_prior_audit_index

        settings.PRIOR_AUDIT_RECONCILE_USERNAME = ""
        assert "skipped" in reconcile_prior_audit_index()

    def test_reports_drift_instead_of_raising(self, settings):
        """Beat should log drift, not crash the worker on a schedule."""
        from connect_labs.audit.prior_audit_tasks import reconcile_prior_audit_index

        settings.PRIOR_AUDIT_RECONCILE_USERNAME = "poller"
        with patch(
            "connect_labs.audit.prior_audit_tasks.call_command",
            side_effect=CommandError("2 opportunit(ies) drifted"),
        ):
            assert reconcile_prior_audit_index().startswith("drift:")

    def test_does_not_repair_by_default(self, settings):
        from connect_labs.audit.prior_audit_tasks import reconcile_prior_audit_index

        settings.PRIOR_AUDIT_RECONCILE_USERNAME = "poller"
        with patch("connect_labs.audit.prior_audit_tasks.call_command") as spy:
            reconcile_prior_audit_index()
        assert "--repair" not in spy.call_args[0]
