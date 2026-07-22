"""Unit tests for the AI-flagged-fraud SUSPENSION fate.

A coaching arc that closes with ``follow_up_outcome_action: "suspended"`` is a
distinct, SOP-breaching fate (the Eastern/Vida nutrition-demo case): the tasks
ensurer seeds a ``closed_suspended_fraud`` task, the run-audits ensurer seeds a
``completed_fail_framing`` audit (5/5 failed photos), and — via the PAR render
code — the cluster reads BELOW even though every week ran. These unit tests pin
the two pure selector functions + the reconcile matcher that drive that fate,
plus the manifest field that expresses it.
"""

from __future__ import annotations

from types import SimpleNamespace

from connect_labs.labs.synthetic.ensure.ensurers.run_audits import _audit_archetype_for, _audit_matches_archetype
from connect_labs.labs.synthetic.ensure.ensurers.tasks import _archetype_for_arc
from connect_labs.labs.synthetic.generator.fixtures.manifest import CoachingArc


def _arc(*, outcome_week, action=None):
    return SimpleNamespace(follow_up_outcome_week=outcome_week, follow_up_outcome_action=action)


def _audit(status, *, pass_=0, fail=0, pending=0):
    return SimpleNamespace(
        status=status,
        data={"image_results": {"pass": pass_, "fail": fail, "pending": pending}},
    )


# ---------- tasks: _archetype_for_arc ----------


def test_task_archetype_suspended_when_action_is_suspended():
    assert _archetype_for_arc(_arc(outcome_week=4, action="suspended")) == "closed_suspended_fraud"


def test_task_archetype_satisfactory_when_closed_without_suspension():
    assert _archetype_for_arc(_arc(outcome_week=3)) == "closed_satisfactory"
    assert _archetype_for_arc(_arc(outcome_week=3, action="satisfactory")) == "closed_satisfactory"


def test_task_archetype_investigating_when_open_regardless_of_action():
    # An open arc is investigating even if a stray action is set.
    assert _archetype_for_arc(_arc(outcome_week=None)) == "investigating"
    assert _archetype_for_arc(_arc(outcome_week=None, action="suspended")) == "investigating"


# ---------- run_audits: _audit_archetype_for ----------


def test_audit_archetype_fraud_for_suspended_flw():
    got = _audit_archetype_for("vida_e", resolved_flws=set(), investigating_flws=set(), suspended_flws={"vida_e"})
    assert got == "completed_fail_framing"


def test_audit_archetype_suspended_takes_precedence_over_resolved():
    # Defensive: even if a flw appears in both sets, suspension wins.
    got = _audit_archetype_for("vida_e", resolved_flws={"vida_e"}, investigating_flws=set(), suspended_flws={"vida_e"})
    assert got == "completed_fail_framing"


def test_audit_archetype_resolved_and_investigating_unchanged():
    assert _audit_archetype_for("kadi_c", {"kadi_c"}, set(), set()) == "completed_pass_clean"
    assert _audit_archetype_for("lola_c", set(), {"lola_c"}, set()) == "in_review_mixed"
    assert _audit_archetype_for("uche_e", set(), set(), set()) == "pending_all_clean"


# ---------- run_audits: _audit_matches_archetype (reconcile) ----------


def test_stale_pass_clean_audit_is_rebuilt_for_fraud_target():
    # A previously-seeded all-pass completed audit must NOT match the fraud target,
    # so reconcile rebuilds it with failed photos (the Eastern re-seed path).
    old = _audit("completed", pass_=5, fail=0)
    assert _audit_matches_archetype(old, "completed_fail_framing") is False


def test_fraud_audit_matches_fraud_target_and_not_resolved():
    fraud = _audit("completed", pass_=0, fail=5)
    assert _audit_matches_archetype(fraud, "completed_fail_framing") is True
    # A fail-completed audit is NOT a clean resolution.
    assert _audit_matches_archetype(fraud, "completed_pass_clean") is False


def test_pass_clean_audit_matches_resolved_target():
    clean = _audit("completed", pass_=5, fail=0)
    assert _audit_matches_archetype(clean, "completed_pass_clean") is True


# ---------- manifest field ----------


def test_coaching_arc_parses_suspended_action():
    arc = CoachingArc(
        flw_id="vida_e",
        week_triggered=3,
        persona="coaching_repeat_offense_fraud_suspension",
        target_behavior="Misleading MUAC photos (suspected fraud)",
        transcript=[],
        follow_up_outcome_week=4,
        follow_up_outcome_action="suspended",
    )
    assert arc.follow_up_outcome_action == "suspended"


def test_coaching_arc_action_defaults_to_none():
    arc = CoachingArc(
        flw_id="kadi_c",
        week_triggered=2,
        persona="coaching_resolved_clean",
        target_behavior="Bad MUAC distribution",
        transcript=[],
        follow_up_outcome_week=3,
    )
    assert arc.follow_up_outcome_action is None
