"""Tests for per-image-type agent resolution in _run_ai_review_on_sessions."""
import pytest

from commcare_connect.audit import tasks
from commcare_connect.labs.ai_review_agents.types import ReviewResult


class _FakeSession:
    def __init__(self, data):
        self.data = data
        self.assessments = []  # (visit_id, blob_id, question_id, result, ai_result, ai_notes)

    def set_assessment(self, visit_id, blob_id, question_id, result, notes, ai_result=None, ai_notes=None):
        self.assessments.append((visit_id, blob_id, question_id, result, ai_result, ai_notes))


class _FakeDataAccess:
    def __init__(self, session):
        self._session = session

    def get_audit_session(self, session_id):
        return self._session

    def download_image_from_connect(self, blob_id, opp_id):
        return b"\xff\xd8fakejpeg"

    def save_audit_session(self, session):
        pass


class _MatchAgent:
    """Stand-in agent that records which blob_ids it was asked to review."""

    name = "Match Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen = []

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        return ReviewResult.success(match=True)


class _OtherAgent(_MatchAgent):
    name = "Other Agent"
    seen = []


@pytest.fixture
def patched_registry(monkeypatch):
    agents = {"agent_a": _MatchAgent(), "agent_b": _OtherAgent()}
    _MatchAgent.seen = []
    _OtherAgent.seen = []
    from commcare_connect.labs.ai_review_agents import registry

    monkeypatch.setattr(registry, "get_agent", lambda aid: agents[aid])
    return agents


def _session_with_two_image_types():
    return _FakeSession(
        {
            "visit_images": {
                "1": [
                    {"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []},
                    {"blob_id": "blobB", "question_id": "form/photo_b", "related_fields": []},
                ]
            }
        }
    )


def test_each_image_type_runs_only_its_reviewer(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": {"agent_id": "agent_a", "auto_apply_actions": ["ok"]},
        "form/photo_b": {"agent_id": "agent_b", "auto_apply_actions": ["ok"]},
    }

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]
    assert _OtherAgent.seen == ["blobB"]


def test_image_type_without_reviewer_is_skipped(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)
    ai_reviewers = {"form/photo_a": {"agent_id": "agent_a", "auto_apply_actions": ["ok"]}}

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]
    assert _OtherAgent.seen == []  # photo_b had no reviewer
    # Only the reviewed image produced an assessment
    assert [a[1] for a in session.assessments] == ["blobA"]


def test_legacy_single_agent_still_runs_on_all(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_agent_id="agent_a",
        auto_apply_actions=["ok"],
    )

    assert sorted(_MatchAgent.seen) == ["blobA", "blobB"]
