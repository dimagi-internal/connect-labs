"""Diagnostics emitted by _run_ai_review_on_sessions.

These cover the two run outcomes that were previously indistinguishable from
success in the logs:

* A run where every image was SKIPPED because no reviewer's configured
  comparison field carried a value. It finishes in seconds, reports success, and
  produces an empty audit -- a configuration mistake that used to be discoverable
  only by a human opening the audit and finding nothing in it.
* A run where a large share of images ERRORED. The count was recorded but not the
  cause, so telling a saturated gateway apart from a dead one meant querying
  per-agent log lines that carry no session or blob id to join against.
"""
import logging

import pytest

from connect_labs.audit import tasks
from connect_labs.labs.ai_review_agents.types import ReviewResult


class _FakeSession:
    def __init__(self, data, id=1):
        self.data = data
        self.assessments = []
        self.id = id
        self.workflow_run_id = None
        self.opportunity_id = 42
        self.opportunity_name = "Test Opp"

    def set_assessment(self, visit_id, blob_id, question_id, result, notes, **kw):
        self.assessments.append((visit_id, blob_id, question_id, result))


class _FakeDataAccess:
    def __init__(self, session, download=None):
        self._session = session
        self._download = download

    def get_audit_session(self, session_id, try_multiple_opportunities=False):
        return self._session

    def download_image_from_connect(self, blob_id, opp_id):
        if self._download is not None:
            return self._download(blob_id)
        return b"\xff\xd8fakejpeg"

    def save_audit_session(self, session):
        pass


class _TimeoutAgent:
    """Errors the way the gateway does in production."""

    agent_id = "agent_timeout"
    name = "Timeout Agent"
    requires_reading = False
    result_actions = {}

    def review(self, ctx):
        return ReviewResult.error("AI classifier service timed out. Try again.", error_kind="timeout")


class _UnclassifiedErrorAgent:
    """An agent predating the taxonomy -- must still be counted, as 'unknown'."""

    agent_id = "agent_old"
    name = "Legacy Agent"
    requires_reading = False
    result_actions = {}

    def review(self, ctx):
        return ReviewResult.error("boom")


class _ReadingAgent:
    agent_id = "agent_reading"
    name = "Reading Agent"
    requires_reading = True
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}

    def review(self, ctx):
        return ReviewResult.success(match=True)


@pytest.fixture(autouse=True)
def _dont_actually_back_off(monkeypatch):
    """Take the retry-sweep backoff off the clock.

    Nothing in this file asserts anything about waiting. But five of these tests
    drive ``agent_timeout``, and ``timeout`` is a saturation kind, so each one
    tripped the real ``_RETRY_SWEEP_BACKOFF_SECONDS`` sleep in
    ``_run_ai_review_on_sessions`` -- 30 seconds of wall clock, five times, for
    assertions about log lines and error tallies. That was ~150s of every CI run
    and every local run, buying no coverage: the backoff itself is deliberately
    tested in test_ai_review_retry_sweep.py, which records the sleeps instead of
    taking them and asserts both their total and their slice size.

    Same idiom as that file, so the two stay recognisable as the same trick.
    """
    monkeypatch.setattr(tasks.time, "sleep", lambda _seconds: None)


@pytest.fixture
def patched_registry(monkeypatch):
    agents = {
        "agent_timeout": _TimeoutAgent(),
        "agent_old": _UnclassifiedErrorAgent(),
        "agent_reading": _ReadingAgent(),
    }
    from connect_labs.labs.ai_review_agents import registry

    monkeypatch.setattr(registry, "get_agent", lambda aid: agents[aid])
    return agents


def _session(images):
    return _FakeSession({"visit_images": {"1": images}})


def _image(blob_id, question_id="form/photo", related_fields=None):
    return {"blob_id": blob_id, "question_id": question_id, "related_fields": related_fields or []}


def _run(data_access, ai_reviewers, **kw):
    return tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
        **kw,
    )


# ---------------------------------------------------------------------------
# Error causes
# ---------------------------------------------------------------------------


def test_error_kinds_are_tallied_and_returned(patched_registry):
    data_access = _FakeDataAccess(_session([_image("b1"), _image("b2")]))

    result = _run(data_access, {"form/photo": [{"agent_id": "agent_timeout"}]})

    assert result["total_errors"] == 2
    assert result["error_kinds"] == {"timeout": 2}


def test_error_breakdown_is_logged(patched_registry, caplog):
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    data_access = _FakeDataAccess(_session([_image("b1")]))

    _run(data_access, {"form/photo": [{"agent_id": "agent_timeout"}]})

    assert any("Error breakdown: timeout=1" in r.message for r in caplog.records)


def test_agents_without_an_error_kind_are_counted_as_unknown(patched_registry):
    """A cause-less error must not silently vanish from the tally -- otherwise
    the breakdown would under-report and quietly disagree with total_errors."""
    data_access = _FakeDataAccess(_session([_image("b1")]))

    result = _run(data_access, {"form/photo": [{"agent_id": "agent_old"}]})

    assert result["error_kinds"] == {"unknown": 1}
    assert sum(result["error_kinds"].values()) == result["total_errors"]


def test_summary_reports_attempted_rather_than_reviewed(patched_registry, caplog):
    """'reviewed=263' alongside 'errors=160' has been read as 103 images going
    missing. It is one number -- attempts -- of which the errors are a part."""
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    data_access = _FakeDataAccess(_session([_image("b1")]))

    _run(data_access, {"form/photo": [{"agent_id": "agent_timeout"}]})

    summary = next(r.message for r in caplog.records if " Complete in " in r.message)
    assert "attempted=1" in summary
    assert "errors=1" in summary
    assert "per_image_s=" in summary


# ---------------------------------------------------------------------------
# The silent empty run
# ---------------------------------------------------------------------------


def _misconfigured_reviewers():
    # The reviewer wants form/expected_weight; the data only carries
    # form/actual_weight, so nothing it can compare against is ever found.
    return {"form/photo": [{"agent_id": "agent_reading", "comparison_field": "form/expected_weight"}]}


def _image_with_other_field(blob_id):
    return _image(blob_id, related_fields=[{"path": "form/actual_weight", "value": "1535"}])


def test_all_skipped_run_is_logged_at_error_with_the_field_names(patched_registry, caplog):
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    data_access = _FakeDataAccess(_session([_image_with_other_field("b1"), _image_with_other_field("b2")]))

    result = _run(data_access, _misconfigured_reviewers())

    assert result["total_reviewed"] == 0
    assert result["total_skipped"] == 2

    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("NO IMAGES REVIEWED" in m for m in errors), errors
    # The whole point: name the field that was configured AND the ones that
    # actually carried values, so the fix is readable off the log line.
    detail = next(m for m in errors if "needs reading field" in m)
    assert "form/expected_weight" in detail
    assert "form/actual_weight" in detail
    assert "form/photo" in detail


def test_partial_misconfiguration_warns_without_claiming_the_run_was_empty(patched_registry, caplog):
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    good = _image("b1", related_fields=[{"path": "form/expected_weight", "value": "1535"}])
    data_access = _FakeDataAccess(_session([good, _image_with_other_field("b2")]))

    result = _run(data_access, _misconfigured_reviewers())

    assert result["total_reviewed"] == 1
    assert result["total_skipped"] == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("form/expected_weight" in m and "had no value" in m for m in warnings), warnings


def test_skip_reasons_separate_configuration_from_download_failure(patched_registry):
    """Both used to increment one opaque 'skipped' counter, so an audit that came
    back empty gave no clue whether the config was wrong or the images were
    unreachable -- opposite problems with opposite owners."""

    def _download(blob_id):
        raise RuntimeError("connection reset")

    good = _image("b1", related_fields=[{"path": "form/expected_weight", "value": "1535"}])
    data_access = _FakeDataAccess(_session([good, _image_with_other_field("b2")]), download=_download)

    result = _run(data_access, _misconfigured_reviewers())

    assert result["skip_reasons"] == {
        "image_download_failed": 1,  # b1 resolved a reading, then failed to download
        "no_reviewer_reading": 1,  # b2 never had a usable field
    }


def test_empty_image_body_is_distinguished_from_a_download_exception(patched_registry):
    good = _image("b1", related_fields=[{"path": "form/expected_weight", "value": "1535"}])
    data_access = _FakeDataAccess(_session([good]), download=lambda blob_id: b"")

    result = _run(data_access, _misconfigured_reviewers())

    assert result["skip_reasons"] == {"image_empty": 1}


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def test_log_tag_is_stamped_on_every_line(patched_registry, caplog):
    """Celery reuses worker processes across tasks, so the worker number cannot
    isolate one run's lines when audits overlap."""
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    data_access = _FakeDataAccess(_session([_image("b1")]))

    _run(data_access, {"form/photo": [{"agent_id": "agent_timeout"}]}, log_tag="6af8acb8")

    tagged = [r.message for r in caplog.records if "[AIReview:6af8acb8]" in r.message]
    assert tagged, [r.message for r in caplog.records]
    assert any(" Complete in " in m for m in tagged)


def test_untagged_runs_keep_the_original_prefix(patched_registry, caplog):
    caplog.set_level(logging.INFO, logger="connect_labs.audit.tasks")
    data_access = _FakeDataAccess(_session([_image("b1")]))

    _run(data_access, {"form/photo": [{"agent_id": "agent_timeout"}]})

    assert any("[AIReview] " in r.message for r in caplog.records)
