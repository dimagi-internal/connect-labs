"""Tests for connect_labs/audit/classifier_fail_sync.py's sync_after_save.

Focuses on the has_ai_flag gate that decides whether a session's classifier-fail
rows get synced with the human's review outcome at all -- see the regression
this covers: a co-occurring "error" verdict from one reviewer can mask a
genuine "no_match" from another in the assessment's combined ai_result.
"""

from connect_labs.audit import classifier_fail_sync


class _FakeSession:
    def __init__(self, visit_results, opportunity_id=42, session_id=5):
        self.data = {"visit_results": visit_results}
        self.opportunity_id = opportunity_id
        self.id = session_id
        self.visit_ids = [100]


class _FakeUser:
    username = "reviewer1"


class _FakeRequest:
    user = _FakeUser()


class _FakeDataAccess:
    access_token = "tok"


def _assessment(**overrides):
    base = {"result": "fail", "notes": "confirmed", "ai_result": "no_match", "duplicate_group": None}
    base.update(overrides)
    return base


def test_sync_runs_when_error_masks_a_co_occurring_no_match(monkeypatch):
    """Regression: the assessment's combined ai_result is "error" (error wins
    over no_match in _combine_reviewer_results) even though a real
    classifier-fail row exists for the failing reviewer -- the sync must still
    run so the human's result/notes/override get recorded."""
    session = _FakeSession({"100": {"assessments": {"blobA": _assessment(ai_result="error")}}})
    monkeypatch.setattr(classifier_fail_sync, "resolve_urls_by_blob", lambda **kwargs: {})

    called = {}
    monkeypatch.setattr(
        classifier_fail_sync.s3_export,
        "sync_classifier_fail_outcomes",
        lambda *a, **kw: called.setdefault("args", (a, kw)),
    )

    classifier_fail_sync.sync_after_save(session, _FakeRequest(), _FakeDataAccess())

    assert "args" in called, "sync_classifier_fail_outcomes should have been called"
    args, kwargs = called["args"]
    assert args[0] == 5  # session_id
    assert args[1] == {"blobA": "fail"}  # human_result_by_blob


def test_sync_still_runs_for_a_plain_no_match(monkeypatch):
    """Sanity check the existing (non-error) path still works after widening
    the has_ai_flag condition."""
    session = _FakeSession({"100": {"assessments": {"blobA": _assessment(ai_result="no_match")}}})
    monkeypatch.setattr(classifier_fail_sync, "resolve_urls_by_blob", lambda **kwargs: {})

    called = {}
    monkeypatch.setattr(
        classifier_fail_sync.s3_export,
        "sync_classifier_fail_outcomes",
        lambda *a, **kw: called.setdefault("args", (a, kw)),
    )

    classifier_fail_sync.sync_after_save(session, _FakeRequest(), _FakeDataAccess())

    assert "args" in called


def test_sync_skipped_when_nothing_was_ever_flagged(monkeypatch):
    """A session with no no_match/error/duplicate_group anywhere has nothing
    in classifier_fails.csv to sync -- skip the HQ/Connect/S3 round trips
    entirely (unchanged behavior, just re-verified after widening the gate)."""
    session = _FakeSession({"100": {"assessments": {"blobA": _assessment(ai_result="match")}}})
    monkeypatch.setattr(classifier_fail_sync, "resolve_urls_by_blob", lambda **kwargs: {})

    called = {}
    monkeypatch.setattr(
        classifier_fail_sync.s3_export,
        "sync_classifier_fail_outcomes",
        lambda *a, **kw: called.setdefault("args", (a, kw)),
    )

    classifier_fail_sync.sync_after_save(session, _FakeRequest(), _FakeDataAccess())

    assert "args" not in called
