"""Tests for the AI-review retry sweep in _run_ai_review_on_sessions.

A gateway hiccup that survives post_with_retry's own retries (see
connect_labs.labs.ai_review_agents.base) previously left an image stuck at
ai_result="error" forever -- a later re-run of the AI-review task would skip
the whole session (see the ai_review_complete tests in
test_ai_review_per_type.py). These tests cover the one-extra-attempt sweep
that now runs, within the same task invocation, over images that ended
their first pass in "error".
"""

from connect_labs.audit import tasks
from connect_labs.labs.ai_review_agents.types import ReviewResult


class _FakeSession:
    """Minimal session double, with the id/workflow_run_id/opportunity_id/
    opportunity_name fields _classifier_fail_rows_for reads for a genuine
    (no_match) classifier fail -- unlike the bare stand-in in
    test_ai_review_per_type.py, which never exercises that path with these
    fields populated."""

    id = 10
    workflow_run_id = 999
    opportunity_id = 42
    opportunity_name = "Test Opp"

    def __init__(self, data):
        self.data = data
        self.assessments = []

    def set_assessment(
        self, visit_id, blob_id, question_id, result, notes, ai_result=None, ai_notes=None, ai_confidence=None
    ):
        self.assessments.append((visit_id, blob_id, question_id, result, ai_result, ai_notes, ai_confidence))

    def get_assessment(self, blob_id):
        for a in self.assessments:
            if a[1] == blob_id:
                return a
        return None


class _FakeDataAccess:
    def __init__(self, session):
        self._session = session
        self.saved_data = []

    def get_audit_session(self, session_id, try_multiple_opportunities=False):
        return self._session

    def download_image_from_connect(self, blob_id, opp_id):
        return b"\xff\xd8fakejpeg"

    def save_audit_session(self, session):
        self.saved_data.append(dict(session.data))


def _one_image_session():
    return _FakeSession(
        {"visit_images": {"1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}]}}
    )


class _RecoversOnRetryAgent:
    """Errors on its first call, then succeeds -- simulates a gateway
    hiccup that clears by the time the retry sweep runs."""

    agent_id = "agent_flaky"
    name = "Flaky Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}

    def __init__(self):
        self.calls = 0

    def review(self, ctx):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("could not reach the AI classifier service")
        return ReviewResult.success(match=True)


class _AlwaysErrorsAgent:
    """Errors on every call -- simulates a sustained outage that outlasts
    both the in-call retry and the one-extra batch sweep."""

    agent_id = "agent_down"
    name = "Down Agent"
    requires_reading = False
    result_actions = {}

    def __init__(self):
        self.calls = 0

    def review(self, ctx):
        self.calls += 1
        raise RuntimeError("could not reach the AI classifier service")


class _FailsOnRetryAgent:
    """Errors on its first call, then produces a genuine no_match on the
    retry -- confirms a real classifier fail discovered only on retry still
    reaches the training-data export."""

    agent_id = "agent_fail_on_retry"
    name = "Fail On Retry Agent"
    requires_reading = False
    result_actions = {"nope": {"ai_result": "no_match", "human_result": "fail", "button_label": "Fail"}}

    def __init__(self):
        self.calls = 0

    def review(self, ctx):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("could not reach the AI classifier service")
        return ReviewResult.failure(badge_label="Distinctly Failed")


def _run_with_agent(agent, monkeypatch, session=None):
    from connect_labs.labs.ai_review_agents import registry

    monkeypatch.setattr(registry, "get_agent", lambda aid: agent)
    session = session or _one_image_session()
    data_access = _FakeDataAccess(session)
    ai_reviewers = {"form/photo_a": [{"agent_id": agent.agent_id, "auto_apply_actions": list(agent.result_actions)}]}
    result = tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )
    return result, session, data_access


def test_retry_sweep_recovers_a_transient_error(monkeypatch):
    agent = _RecoversOnRetryAgent()
    result, session, data_access = _run_with_agent(agent, monkeypatch)

    assert agent.calls == 2  # first pass errors, retry sweep succeeds
    assert result["total_errors"] == 0
    assert result["total_passed"] == 1

    assert len(session.assessments) == 2  # first-pass error persisted, then overwritten by the retry
    _visit_id, _blob_id, _qid, result_field, ai_result, _ai_notes, _ai_confidence = session.assessments[-1]
    assert ai_result == "match"
    assert result_field == "pass"

    assert data_access.saved_data[-1].get("ai_review_complete") is True


def test_retry_sweep_still_errors_leaves_error_and_completes_session(monkeypatch):
    agent = _AlwaysErrorsAgent()
    result, session, data_access = _run_with_agent(agent, monkeypatch)

    # Exactly one extra attempt -- not unbounded retrying against a dead gateway.
    assert agent.calls == 2
    assert result["total_errors"] == 1
    assert result["total_passed"] == 0

    _visit_id, _blob_id, _qid, _result_field, ai_result, _ai_notes, _ai_confidence = session.assessments[-1]
    assert ai_result == "error"

    # A sustained outage must not block the batch -- the session is still
    # marked complete so the task doesn't loop or leave the run stuck.
    assert data_access.saved_data[-1].get("ai_review_complete") is True


def test_retry_sweep_no_match_reaches_classifier_fail_export(monkeypatch):
    """A genuine classifier fail discovered only on the retry sweep must
    still produce a training-data row, same as a first-pass no_match would."""
    agent = _FailsOnRetryAgent()
    calls = []
    monkeypatch.setattr(tasks.s3_export, "record_classifier_fails", lambda rows: calls.append(rows))

    result, session, _data_access = _run_with_agent(agent, monkeypatch)

    assert result["total_failed"] == 1
    assert result["total_errors"] == 0
    assert calls and len(calls[0]) == 1
    row = calls[0][0]
    assert row["blob_id"] == "blobA"
    assert row["classifier_label"] == "Distinctly Failed"
    assert row["session_id"] == 10
