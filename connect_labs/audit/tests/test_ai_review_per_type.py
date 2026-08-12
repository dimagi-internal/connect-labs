"""Tests for per-image-type agent resolution in _run_ai_review_on_sessions."""
import pytest
from django.test import override_settings

from connect_labs.audit import tasks
from connect_labs.labs.ai_review_agents.types import ReviewResult

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class _FakeSession:
    def __init__(self, data, id=1, workflow_run_id=None, opportunity_id=42, opportunity_name="Test Opp"):
        self.data = data
        self.assessments = []  # (visit_id, blob_id, question_id, result, ai_result, ai_notes, ai_confidence)
        # Only needed by the classifier_fail_rows path (session.id/etc. -- see
        # tasks.py's fail_verdicts loop) -- other tests in this file don't
        # exercise that path far enough to touch these, but a fail-producing
        # test needs real values rather than a crash-and-swallow AttributeError.
        self.id = id
        self.workflow_run_id = workflow_run_id
        self.opportunity_id = opportunity_id
        self.opportunity_name = opportunity_name
        self.visit_ids = list(data.get("visit_images", {}).keys())

    def set_assessment(
        self, visit_id, blob_id, question_id, result, notes, ai_result=None, ai_notes=None, ai_confidence=None
    ):
        self.assessments.append((visit_id, blob_id, question_id, result, ai_result, ai_notes, ai_confidence))


class _FakeDataAccess:
    def __init__(self, session):
        self._session = session

    def get_audit_session(self, session_id, try_multiple_opportunities=False):
        return self._session

    def download_image_from_connect(self, blob_id, opp_id):
        return b"\xff\xd8fakejpeg"

    def save_audit_session(self, session):
        pass


class _MatchAgent:
    """Stand-in agent that records which blob_ids it was asked to review."""

    agent_id = "agent_a"
    name = "Match Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen = []

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        return ReviewResult.success(match=True)


class _OtherAgent(_MatchAgent):
    agent_id = "agent_b"
    name = "Other Agent"
    seen = []


class _FailAgent:
    """Stand-in agent that always fails, with its own distinct badge label —
    used to verify multiple independent reviewers on one image path."""

    agent_id = "agent_fail"
    name = "Fail Agent"
    requires_reading = False
    result_actions = {"nope": {"ai_result": "no_match", "human_result": "fail", "button_label": "Fail"}}
    seen = []

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        return ReviewResult.failure(badge_label="Distinctly Failed")


class _ErrorAgent:
    """Stand-in agent that always errors -- used to verify a co-occurring
    error verdict from one reviewer doesn't blank out another reviewer's own
    implied result in the classifier-fail export."""

    agent_id = "agent_error"
    name = "Error Agent"
    requires_reading = False
    result_actions = {}
    seen = []

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        return ReviewResult.error("boom")


class _ReadingAgent:
    """Stand-in agent that requires a reading and records what it received —
    used to verify each reviewer gets its OWN configured comparison_field's
    value, not whichever related field happens to come first."""

    agent_id = "agent_reading"
    name = "Reading Agent"
    requires_reading = True
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen_readings = []

    def review(self, ctx):
        type(self).seen_readings.append(ctx.form_data.get("reading"))
        return ReviewResult.success(match=True)


@pytest.fixture
def patched_registry(monkeypatch):
    agents = {
        "agent_a": _MatchAgent(),
        "agent_b": _OtherAgent(),
        "agent_fail": _FailAgent(),
        "agent_error": _ErrorAgent(),
        "agent_reading": _ReadingAgent(),
    }
    _MatchAgent.seen = []
    _OtherAgent.seen = []
    _FailAgent.seen = []
    _ErrorAgent.seen = []
    _ReadingAgent.seen_readings = []
    from connect_labs.labs.ai_review_agents import registry

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
        "form/photo_a": [{"agent_id": "agent_a", "auto_apply_actions": ["ok"]}],
        "form/photo_b": [{"agent_id": "agent_b", "auto_apply_actions": ["ok"]}],
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
    ai_reviewers = {"form/photo_a": [{"agent_id": "agent_a", "auto_apply_actions": ["ok"]}]}

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


def test_two_independent_reviewers_on_one_path_both_run_and_fail_wins(patched_registry):
    """Two reviewers on the same image path (e.g. MUAC OverZoom + MUAC Match)
    both run independently. If one fails and the other passes, the combined
    assessment fails (never silently hidden), the failing reviewer's own
    badge_label survives in the notes, and human_result is 'fail'."""
    session = _FakeSession(
        {
            "visit_images": {
                "1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}],
            }
        }
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [
            {"agent_id": "agent_a", "auto_apply_actions": ["ok"]},
            {"agent_id": "agent_fail", "auto_apply_actions": ["nope"]},
        ],
    }

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]
    assert _FailAgent.seen == ["blobA"]  # both reviewers ran independently

    assert len(session.assessments) == 1
    _visit_id, _blob_id, _qid, result, ai_result, ai_notes, _ai_confidence = session.assessments[0]
    assert ai_result == "no_match"
    assert ai_notes == "Distinctly Failed"
    assert result == "fail"


def test_classifier_fail_rows_get_urls_resolved_before_human_review(patched_registry, monkeypatch):
    """A freshly-recorded classifier-fail row should carry image/form/connect
    URLs resolved right now (once per session), not wait for a human to
    save/complete the session (see connect_labs/audit/classifier_fail_sync.py
    for the pre-existing post-review-only path this feature no longer depends
    on exclusively).

    Uses a session.opportunity_id that DIFFERS from the batch-level opp_id
    (as in a per-FLW multi-opportunity run) -- regression coverage for a bug
    where URL resolution used the wrong opportunity_id, which a same-value
    session/opp_id pairing would silently pass."""
    session = _FakeSession(
        {
            "visit_images": {
                "1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}],
            }
        },
        opportunity_id=99,
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {"form/photo_a": [{"agent_id": "agent_fail", "auto_apply_actions": ["nope"]}]}

    captured_resolve_kwargs = {}

    def _fake_resolve_urls_by_blob(**kwargs):
        captured_resolve_kwargs.update(kwargs)
        return {"blobA": {"image_url": "https://labs/image/blobA", "form_url": "https://hq/blobA"}}

    captured_rows = {}
    monkeypatch.setattr("connect_labs.audit.tasks.resolve_urls_by_blob", _fake_resolve_urls_by_blob)
    monkeypatch.setattr(
        "connect_labs.audit.tasks.s3_export.record_classifier_fails",
        lambda rows: captured_rows.setdefault("rows", rows),
    )

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,  # primary/batch opp_id -- deliberately different from session.opportunity_id
        ai_reviewers=ai_reviewers,
    )

    assert captured_resolve_kwargs["data_access"] is data_access
    assert captured_resolve_kwargs["access_token"] == "tok"
    assert captured_resolve_kwargs["opportunity_id"] == 99  # session's own opp, not the batch opp_id
    assert len(captured_rows["rows"]) == 1
    assert captured_rows["rows"][0]["image_url"] == "https://labs/image/blobA"
    assert captured_rows["rows"][0]["form_url"] == "https://hq/blobA"


def test_classifier_fail_row_uses_its_own_image_opportunity_id(patched_registry, monkeypatch):
    """Regression: a multi-opp combined session (e.g. muac_picture_audit) can
    flag images sourced from different opportunities -- each row's stored
    opportunity_id must be that image's own, not session.opportunity_id."""
    session = _FakeSession(
        {
            "visit_images": {
                "1": [
                    {"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": [], "opportunity_id": 555},
                    {"blob_id": "blobB", "question_id": "form/photo_b", "related_fields": [], "opportunity_id": 777},
                ],
            }
        },
        opportunity_id=1,
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [{"agent_id": "agent_fail", "auto_apply_actions": ["nope"]}],
        "form/photo_b": [{"agent_id": "agent_fail", "auto_apply_actions": ["nope"]}],
    }

    captured_rows = {}
    monkeypatch.setattr("connect_labs.audit.tasks.resolve_urls_by_blob", lambda **kwargs: {})
    monkeypatch.setattr(
        "connect_labs.audit.tasks.s3_export.record_classifier_fails",
        lambda rows: captured_rows.setdefault("rows", rows),
    )

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=1,
        ai_reviewers=ai_reviewers,
    )

    rows_by_blob = {row["blob_id"]: row for row in captured_rows["rows"]}
    assert rows_by_blob["blobA"]["opportunity_id"] == 555
    assert rows_by_blob["blobB"]["opportunity_id"] == 777


def test_classifier_fail_row_uses_its_own_verdicts_implied_result_not_combined(patched_registry, monkeypatch):
    """When a co-occurring reviewer errors on the same image, the combine step
    lets the error win and outcome.human_result comes back None -- but the
    genuinely-failing reviewer's own exported row should still carry ITS OWN
    implied result (from its own auto_apply_actions mapping), not the blanked
    combined value."""
    session = _FakeSession(
        {
            "visit_images": {
                "1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}],
            }
        }
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [
            {"agent_id": "agent_fail", "auto_apply_actions": ["nope"]},
            {"agent_id": "agent_error", "auto_apply_actions": []},
        ],
    }

    captured_rows = {}
    monkeypatch.setattr(
        "connect_labs.audit.tasks.s3_export.record_classifier_fails",
        lambda rows: captured_rows.setdefault("rows", rows),
    )

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    # The combined ai_result is "error" (error wins over no_match), so the
    # assessment's own human_result is None -- but agent_fail's row must still
    # show its own implied "fail", not that blanked combined value.
    assert len(session.assessments) == 1
    assert session.assessments[0][3] is None  # combined human `result` on the assessment
    fail_rows = [r for r in captured_rows["rows"] if r["classifier_id"] == "agent_fail"]
    assert len(fail_rows) == 1
    assert fail_rows[0]["ai_implied_result"] == "fail"


def test_two_reviewers_on_one_image_run_concurrently_not_sequentially(patched_registry, monkeypatch):
    """Regression for the perf fix: two independent reviewers on the same
    image (e.g. MUAC OverZoom + MUAC Match) must run concurrently -- each
    one's blocking HTTP call shouldn't double that image's AI-review latency
    the way sequential per-reviewer calls would."""
    import time

    SLEEP = 0.2

    class _SlowAgentA(_MatchAgent):
        agent_id = "agent_a"
        seen = []

        def review(self, ctx):
            time.sleep(SLEEP)
            return super().review(ctx)

    class _SlowAgentB(_FailAgent):
        agent_id = "agent_fail"
        seen = []

        def review(self, ctx):
            time.sleep(SLEEP)
            return super().review(ctx)

    agents = {"agent_a": _SlowAgentA(), "agent_fail": _SlowAgentB()}
    from connect_labs.labs.ai_review_agents import registry

    monkeypatch.setattr(registry, "get_agent", lambda aid: agents[aid])
    _SlowAgentA.seen = []
    _SlowAgentB.seen = []

    session = _FakeSession(
        {"visit_images": {"1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}]}}
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [
            {"agent_id": "agent_a", "auto_apply_actions": ["ok"]},
            {"agent_id": "agent_fail", "auto_apply_actions": ["nope"]},
        ],
    }

    start = time.monotonic()
    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )
    elapsed = time.monotonic() - start

    assert _SlowAgentA.seen == ["blobA"]
    assert _SlowAgentB.seen == ["blobA"]
    # Sequential would take >= 2*SLEEP; concurrent should take roughly 1*SLEEP.
    # Generous upper bound to avoid CI timing flakiness.
    assert elapsed < SLEEP * 1.8, f"expected concurrent reviewer calls, took {elapsed:.3f}s for 2x{SLEEP}s calls"


def test_reading_required_reviewer_skips_itself_when_its_field_is_absent(patched_registry):
    """The actual production pairing being wired up (muac_overzoom, requires_reading=
    False, + muac_match, requires_reading=True) on the same path: if no related
    field carries a value, the reading-requiring reviewer skips itself but the
    other reviewer still runs and its verdict alone determines the assessment."""
    session = _FakeSession(
        {"visit_images": {"1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}]}}
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [
            {"agent_id": "agent_a", "auto_apply_actions": ["ok"]},  # requires_reading=False
            {"agent_id": "agent_reading", "auto_apply_actions": ["ok"], "comparison_field": "form/reading"},
        ],
    }

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]  # ran despite no reading — it doesn't need one
    assert _ReadingAgent.seen_readings == []  # skipped itself — no reading available
    assert len(session.assessments) == 1  # the image is still reviewed, not dropped entirely


def test_each_reviewer_gets_its_own_comparison_field_value(patched_registry):
    """Regression for the critical finding: when related_fields carries values
    for MULTIPLE fields, a reviewer must receive its OWN configured
    comparison_field's value — never whichever related field happens to be
    first in the list."""
    session = _FakeSession(
        {
            "visit_images": {
                "1": [
                    {
                        "blob_id": "blobA",
                        "question_id": "form/photo_a",
                        "related_fields": [
                            {"path": "form/field_one", "value": "111"},
                            {"path": "form/field_two", "value": "222"},
                        ],
                    }
                ]
            }
        }
    )
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": [
            {"agent_id": "agent_reading", "auto_apply_actions": ["ok"], "comparison_field": "form/field_two"},
        ],
    }

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    # Must receive "222" (its own configured field), never "111" (a different
    # field that happens to come first in related_fields).
    assert _ReadingAgent.seen_readings == ["222"]


class TestCombineReviewerResults:
    def test_both_pass_joins_pass_labels_and_keeps_first_confidence(self):
        results = [
            tasks.ReviewerVerdict("a", "match", "A Pass", 0.9, {}),
            tasks.ReviewerVerdict("b", "match", "B Pass", 0.8, {}),
        ]
        ai_result, ai_notes, ai_confidence, human_result = tasks._combine_reviewer_results(results)
        assert ai_result == "match"
        assert ai_notes == "A Pass; B Pass"
        assert ai_confidence == 0.9
        assert human_result is None

    def test_both_fail_joins_badge_labels(self):
        results = [
            tasks.ReviewerVerdict("a", "no_match", "A Fail", None, {"no_match": "fail"}),
            tasks.ReviewerVerdict("b", "no_match", "B Fail", None, {}),
        ]
        ai_result, ai_notes, _ai_confidence, human_result = tasks._combine_reviewer_results(results)
        assert ai_result == "no_match"
        assert ai_notes == "A Fail; B Fail"
        assert human_result == "fail"

    def test_error_wins_over_fail_and_pass(self):
        results = [
            tasks.ReviewerVerdict("a", "match", "Pass", None, {}),
            tasks.ReviewerVerdict("b", "no_match", "Fail", None, {}),
            tasks.ReviewerVerdict("c", "error", "Boom", None, {}),
        ]
        ai_result, ai_notes, _ai_confidence, _human_result = tasks._combine_reviewer_results(results)
        assert ai_result == "error"
        assert ai_notes == "Boom"

    def test_human_result_never_contradicts_ai_result(self):
        """Regression for the design review's critical finding: a passing
        reviewer whose auto_apply_actions maps 'match'->'pass' must NOT set
        human_result='pass' when a DIFFERENT reviewer's failure decided
        ai_result='no_match'. human_result only draws from the winning
        bucket's own reviewers, never an independent poll of all of them."""
        results = [
            tasks.ReviewerVerdict("fails_flag_only", "no_match", "Failed", None, {}),  # no auto-apply for its own fail
            tasks.ReviewerVerdict("passes_auto_applied", "match", "Passed", None, {"match": "pass"}),
        ]
        ai_result, _ai_notes, _ai_confidence, human_result = tasks._combine_reviewer_results(results)
        assert ai_result == "no_match"
        assert human_result is None  # NOT "pass" — the passing reviewer isn't in the winning bucket

    def test_raises_on_empty_input(self):
        with pytest.raises(ValueError):
            tasks._combine_reviewer_results([])

    def test_join_round_trips_through_get_assessment_stats(self):
        """Regression for the aggregation-breaking bug the design review
        caught: _combine_reviewer_results' real join output, fed straight
        into get_assessment_stats(), must recover each reviewer's own label
        with its own count -- proving the two functions' shared
        AI_NOTES_JOIN_SEP contract actually holds end-to-end, not just via
        hand-written fixtures on either side."""
        from connect_labs.audit.models import AuditSessionRecord

        results = [
            tasks.ReviewerVerdict("muac_overzoom", "no_match", "Hyperzoomed", None, {}),
            tasks.ReviewerVerdict("muac_match", "no_match", "MUAC Mismatch (strict tolerance)", None, {}),
        ]
        _ai_result, ai_notes, _ai_confidence, human_result = tasks._combine_reviewer_results(results)

        session = AuditSessionRecord(
            {
                "id": 1,
                "experiment": "audit",
                "type": "AuditSession",
                "data": {
                    "visit_results": {
                        "1": {
                            "assessments": {
                                "blobA": {
                                    "result": human_result,
                                    "question_id": "form/muac_photo",
                                    "ai_result": "no_match",
                                    "ai_notes": ai_notes,
                                }
                            }
                        }
                    }
                },
                "opportunity_id": 1973,
            }
        )
        stats = session.get_assessment_stats()
        assert stats["ai_flags_by_label"] == {
            "Hyperzoomed": 1,
            "MUAC Mismatch (strict tolerance)": 1,
        }


class _CancelAfterReviewAgent:
    """Stand-in agent that flags cancellation itself after reviewing one image
    -- simulates the user clicking Stop while the first session is still
    being reviewed, so the SECOND session's turn in the outer loop must never
    start."""

    agent_id = "agent_cancel_trigger"
    name = "Cancel Trigger Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen = []

    def review(self, ctx):
        from connect_labs.audit.data_access import mark_audit_creation_cancelled

        type(self).seen.append(ctx.metadata["blob_id"])
        mark_audit_creation_cancelled("test-cancel-key")
        return ReviewResult.success(match=True)


@override_settings(CACHES=_LOCMEM)
def test_cancel_key_stops_before_next_session(monkeypatch):
    from django.core.cache import cache

    cache.clear()
    _CancelAfterReviewAgent.seen = []
    monkeypatch.setattr(
        "connect_labs.labs.ai_review_agents.registry.get_agent",
        lambda aid: {"agent_cancel_trigger": _CancelAfterReviewAgent()}[aid],
    )

    session_1 = _FakeSession(
        {"visit_images": {"1": [{"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []}]}}
    )
    session_2 = _FakeSession(
        {"visit_images": {"1": [{"blob_id": "blobB", "question_id": "form/photo_a", "related_fields": []}]}}
    )
    sessions_by_id = {10: session_1, 20: session_2}

    class _MultiSessionDataAccess:
        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return sessions_by_id[session_id]

        def download_image_from_connect(self, blob_id, opp_id):
            return b"\xff\xd8fakejpeg"

        def save_audit_session(self, session):
            pass

    result = tasks._run_ai_review_on_sessions(
        data_access=_MultiSessionDataAccess(),
        session_ids=[10, 20],
        access_token="tok",
        opp_id=42,
        ai_reviewers={"form/photo_a": [{"agent_id": "agent_cancel_trigger", "auto_apply_actions": ["ok"]}]},
        cancel_key="test-cancel-key",
    )

    # Session 1 was reviewed (the flag is only set from inside its own
    # review call); session 2 must never have started.
    assert _CancelAfterReviewAgent.seen == ["blobA"]
    assert result["cancelled"] is True
    assert result["total_reviewed"] == 1


class _CancelOnFirstAgent:
    """Sets the cancel flag from the FIRST review() call it sees, then keeps
    reviewing (as any real agent would) -- exercises the mid-session path
    where later, still-queued futures get .cancel()'d rather than run."""

    agent_id = "agent_cancel_multi"
    name = "Cancel On First Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen = []
    flagged = False

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        if not type(self).flagged:
            type(self).flagged = True
            from connect_labs.audit.data_access import mark_audit_creation_cancelled

            mark_audit_creation_cancelled("test-cancel-key-multi")
        return ReviewResult.success(match=True)


@override_settings(CACHES=_LOCMEM)
def test_cancel_key_drops_still_queued_futures_mid_session(monkeypatch):
    """With enough images to keep the outer pool's workers saturated (a 4x
    margin over _MAX_CONCURRENT_IMAGES_PER_SESSION, matching this pool's own
    sizing), some futures are still queued (never started) when the flag
    flips -- those must be .cancel()'d rather than run, so far fewer than all
    images get reviewed. The margin is derived from the production constant
    so this stays a meaningful test if that constant is retuned again."""
    from django.core.cache import cache

    cache.clear()
    _CancelOnFirstAgent.seen = []
    _CancelOnFirstAgent.flagged = False
    monkeypatch.setattr(
        "connect_labs.labs.ai_review_agents.registry.get_agent",
        lambda aid: {"agent_cancel_multi": _CancelOnFirstAgent()}[aid],
    )

    n_images = tasks._MAX_CONCURRENT_IMAGES_PER_SESSION * 4
    images = [{"blob_id": f"blob{i}", "question_id": "form/photo_a", "related_fields": []} for i in range(n_images)]
    session = _FakeSession({"visit_images": {"1": images}})

    class _SingleSessionDataAccess:
        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def download_image_from_connect(self, blob_id, opp_id):
            return b"\xff\xd8fakejpeg"

        def save_audit_session(self, session):
            pass

    result = tasks._run_ai_review_on_sessions(
        data_access=_SingleSessionDataAccess(),
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers={"form/photo_a": [{"agent_id": "agent_cancel_multi", "auto_apply_actions": ["ok"]}]},
        cancel_key="test-cancel-key-multi",
    )

    assert result["cancelled"] is True
    # Bounded, not exact: at most _MAX_CONCURRENT_IMAGES_PER_SESSION were already
    # running when the flag flipped; the rest of n_images were still queued.
    assert result["total_reviewed"] < n_images
    assert len(_CancelOnFirstAgent.seen) < n_images


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
