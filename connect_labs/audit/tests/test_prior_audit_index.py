"""Unit tests for the prior-audit index (Must-have #5)."""
from connect_labs.audit.data_access import build_prior_audit_index
from connect_labs.audit.models import AuditSessionRecord


def _session(id, status, visit_results, completed_at=None, title=""):
    data = {"status": status, "visit_results": visit_results, "title": title}
    if completed_at:
        data["completed_at"] = completed_at
    return AuditSessionRecord({"id": id, "experiment": "audit", "type": "AuditSession",
                               "opportunity_id": 1973, "data": data})


def _vr(**assessments):
    # assessments: blob_id -> result
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def test_indexes_verdicts_from_completed_sessions():
    s = _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at="2026-05-01T00:00:00Z")
    index = build_prior_audit_index([s])
    assert index["111:b1"]["result"] == "pass"
    assert index["111:b2"]["result"] == "fail"
    assert index["111:b1"]["session_id"] == 1


def test_ignores_in_progress_sessions():
    s = _session(1, "in_progress", {"111": _vr(b1="pass")})
    assert build_prior_audit_index([s]) == {}


def test_ignores_pending_images():
    s = _session(1, "completed", {"111": _vr(b1=None, b2="")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s]) == {}


def test_duplicate_fake_counts_as_audited():
    s = _session(1, "completed", {"111": _vr(b1="duplicate_fake")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s])["111:b1"]["result"] == "duplicate_fake"


def test_excludes_named_session():
    s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
    assert build_prior_audit_index([s], exclude_session_id=7) == {}


def test_most_recent_verdict_wins():
    old = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at="2026-05-01T00:00:00Z")
    new = _session(2, "completed", {"111": _vr(b1="fail")}, completed_at="2026-06-01T00:00:00Z")
    index = build_prior_audit_index([old, new])
    assert index["111:b1"]["result"] == "fail"
    assert index["111:b1"]["session_id"] == 2
