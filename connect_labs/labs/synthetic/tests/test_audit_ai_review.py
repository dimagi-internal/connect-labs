"""Synthetic audits carry real AI-review metadata.

A real MUAC audit is reviewed by the ``muac_overzoom`` AI agent at creation: each
photo gets an ``ai_result`` ("match"/"no_match"), an ``ai_notes`` label
("Not Hyperzoomed"/"Hyperzoomed") and an ``ai_confidence``, and the session carries
``has_ai_reviewer=True``. These tests pin that the synthetic ``build_audit_data``
reproduces that shape for the AI-reviewed archetypes (so the audit page renders the
AI badge + reason + confidence, not a bare human fail), and that the suspended-fraud
audit uses the FRAMING (hyperzoomed) images the agent actually flags.
"""

from __future__ import annotations

from connect_labs.labs.synthetic.archetypes import build_audit_data
from connect_labs.labs.synthetic.ensure.ensurers.run_audits import _SUSPENDED_FRAUD_AUDIT_ARCHETYPE


def _all_assessments(data):
    out = {}
    for vr in data["visit_results"].values():
        out.update(vr.get("assessments", {}))
    return out


def _build(archetype_name, seed=7):
    return build_audit_data(
        archetype_name=archetype_name,
        flw_id="vida_e",
        monday_iso="2026-05-18",
        opportunity_id=10012,
        opportunity_name="Eastern Cluster",
        workflow_run_id=999,
        visit_id_base=4242,
        rng_seed=seed,
        flw_name="Vida Kargbo",
    )


def test_suspended_fraud_uses_the_ai_flaggable_framing_archetype():
    # The suspension audit must use framing (hyperzoomed) images — the category the
    # muac_overzoom agent genuinely flags — so the "Hyperzoomed" badge matches the image.
    assert _SUSPENDED_FRAUD_AUDIT_ARCHETYPE == "completed_fail_framing"


def test_fraud_audit_carries_ai_no_match_hyperzoomed_on_every_photo():
    data = _build("completed_fail_framing")
    assert data["has_ai_reviewer"] is True
    assessments = _all_assessments(data)
    assert len(assessments) == 5
    for a in assessments.values():
        assert a["result"] == "fail"  # human confirmed
        assert a["ai_result"] == "no_match"  # AI flagged
        assert a["ai_notes"] == "Hyperzoomed"
        assert 0.86 <= a["ai_confidence"] <= 0.98


def test_pass_clean_audit_carries_ai_match_not_hyperzoomed():
    data = _build("completed_pass_clean")
    assert data["has_ai_reviewer"] is True
    assessments = _all_assessments(data)
    assert len(assessments) == 5
    for a in assessments.values():
        assert a["result"] == "pass"
        assert a["ai_result"] == "match"
        assert a["ai_notes"] == "Not Hyperzoomed"
        assert 0.86 <= a["ai_confidence"] <= 0.98


def test_non_ai_reviewed_archetype_has_no_ai_metadata():
    # completed_fail_tape_usage is NOT ai_reviewed (its bads aren't hyperzoomed, so
    # the AI would not flag them — only a human fails them).
    data = _build("completed_fail_tape_usage")
    assert "has_ai_reviewer" not in data
    for a in _all_assessments(data).values():
        assert a["ai_result"] == ""
        assert a["ai_notes"] == ""
        assert "ai_confidence" not in a


def test_in_review_mixed_ai_reviews_only_the_decided_photos():
    # 2 pass + 1 fail decided (+ 2 pending). AI metadata attaches to the 3 decided
    # photos; pending photos stay assessment-less (still pending for the human).
    data = _build("in_review_mixed")
    assert data["has_ai_reviewer"] is True
    assessments = _all_assessments(data)
    assert len(assessments) == 3
    for a in assessments.values():
        assert a["ai_result"] in ("match", "no_match")
        assert a["ai_notes"] in ("Not Hyperzoomed", "Hyperzoomed")


def test_ai_confidence_is_deterministic_for_same_seed():
    a = _all_assessments(_build("completed_fail_framing", seed=11))
    b = _all_assessments(_build("completed_fail_framing", seed=11))
    assert {k: v["ai_confidence"] for k, v in a.items()} == {k: v["ai_confidence"] for k, v in b.items()}
