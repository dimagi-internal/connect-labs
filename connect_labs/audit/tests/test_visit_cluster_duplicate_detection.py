"""Unit tests for connect_labs.audit.visit_cluster_duplicate_detection.

Uses the real AuditSessionRecord (matching the style of PR #1070's
test_duplicate_detection.py) since _mark_duplicate now delegates the actual
assessment read/modify/write to AuditSessionRecord.flag_potential_duplicate /
flag_potential_duplicate_and_tag rather than poking session.data directly --
these tests are about the grouping/dispatch logic and the "also tag result"
behavior layered on top of PR #1070's flag-only method, not persistence.
"""

from unittest.mock import Mock

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.visit_cluster_duplicate_detection import _mark_duplicate, run_grouping_duplicate_detection


def _session(visit_results=None, opportunity_id=1973):
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": opportunity_id,
            "data": {"visit_results": visit_results or {}},
        }
    )


def _target(session, clusters, blob_meta_by_id, opp_id=1973, data_access=None):
    """run_grouping_duplicate_detection re-fetches via data_access.get_audit_session()
    rather than mutating `session` directly (see the docstring on
    run_grouping_duplicate_detection) -- default get_audit_session to hand back the
    SAME session object, so tests that aren't specifically modeling a stale-
    vs-fresh mismatch (see test_run_duplicate_detection_preserves...) behave
    as if nothing else touched the session between creation and this stage."""
    data_access = data_access or Mock()
    data_access.get_audit_session.return_value = session
    return {
        "session": session,
        "data_access": data_access,
        "opp_id": opp_id,
        "clusters": clusters,
        "blob_meta_by_id": blob_meta_by_id,
    }


# ── _mark_duplicate ──────────────────────────────────────────────────────


def test_mark_duplicate_on_untouched_assessment_sets_result_and_ai_fields():
    session = _session()
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    assert _mark_duplicate(session, blob_meta, "a", group_id=0) is True

    assessment = session.data["visit_results"]["111"]["assessments"]["a"]
    assert assessment["result"] == "duplicate_fake"
    assert assessment["ai_result"] == "no_match"
    assert assessment["ai_notes"] == "Potential Duplicate"  # PR #1070's shared label
    assert assessment["question_id"] == "form/muac"
    assert assessment["duplicate_group"] == 0


def test_mark_duplicate_never_overwrites_existing_human_result():
    session = _session({"111": {"assessments": {"a": {"question_id": "form/muac", "result": "pass", "notes": ""}}}})
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a", group_id=0)

    assert session.data["visit_results"]["111"]["assessments"]["a"]["result"] == "pass"
    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_result"] == "no_match"


def test_mark_duplicate_appends_to_existing_ai_notes_without_duplicating_label():
    session = _session(
        {
            "111": {
                "assessments": {
                    "a": {
                        "question_id": "form/muac",
                        "result": None,
                        "notes": "",
                        "ai_result": "no_match",
                        "ai_notes": "Hyperzoomed",
                    }
                }
            }
        }
    )
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a", group_id=0)
    assessment = session.data["visit_results"]["111"]["assessments"]["a"]
    assert assessment["ai_notes"] == "Hyperzoomed; Potential Duplicate"

    # Idempotent -- marking the same blob twice doesn't duplicate the label.
    _mark_duplicate(session, blob_meta, "a", group_id=0)
    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_notes"] == "Hyperzoomed; Potential Duplicate"


def test_mark_duplicate_inherits_flag_potential_duplicates_unconditional_ai_result_overwrite():
    """AuditSessionRecord.flag_potential_duplicate (PR #1070) unconditionally
    sets ai_result="no_match", even over a prior "error" -- a known, already
    flagged (CodeRabbit nitpick on #1070) limitation this module inherits by
    delegating to that method rather than re-implementing its own merge. Pins
    the current (inherited) behavior honestly rather than silently dropping
    coverage; revisit if/when #1070's own method is tightened."""
    session = _session(
        {
            "111": {
                "assessments": {"a": {"question_id": "form/muac", "result": None, "notes": "", "ai_result": "error"}}
            }
        }
    )
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a", group_id=0)

    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_result"] == "no_match"


def test_mark_duplicate_returns_false_for_unknown_blob():
    session = _session()
    assert _mark_duplicate(session, {}, "unknown-blob", group_id=0) is False
    assert session.data["visit_results"] == {}


# ── run_grouping_duplicate_detection ──────────────────────────────────────────────


def test_skips_groupings_with_fewer_than_two_images():
    session = _session()
    clusters = [{"group_id": "g1", "visit_ids": [111], "image_count": 1, "image_ids": ["a"]}]
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}
    client = Mock()

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    client.detect.assert_not_called()
    assert result == {
        "groupings_checked": 0,
        "groupings_skipped": 1,
        "skipped_over_limit": 0,
        "images_flagged": 0,
        "errors": 0,
        "cancelled": False,
    }


def test_calls_api_once_per_grouping_and_flags_returned_ids():
    session = _session()
    clusters = [
        {"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]},
        {"group_id": "g2", "visit_ids": [113, 114], "image_count": 2, "image_ids": ["c", "d"]},
    ]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
        "c": {"visit_id": 113, "question_id": "form/other"},
        "d": {"visit_id": 114, "question_id": "form/other"},
    }
    client = Mock()
    client.detect.side_effect = [[["a", "b"]], []]
    data_access = Mock()

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta, data_access=data_access)],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
    )

    assert client.detect.call_count == 2
    first_call_images = client.detect.call_args_list[0][0][0]
    assert {img["id"] for img in first_call_images} == {"a", "b"}
    assert result["groupings_checked"] == 2
    assert result["groupings_skipped"] == 0
    assert result["images_flagged"] == 2
    assert result["errors"] == 0
    assert session.data["visit_results"]["111"]["assessments"]["a"]["result"] == "duplicate_fake"
    assert session.data["visit_results"]["112"]["assessments"]["b"]["result"] == "duplicate_fake"
    assert "113" not in session.data["visit_results"]
    data_access.save_audit_session.assert_called_once_with(session)


def test_overlapping_response_groups_are_merged_into_one_component():
    """The endpoint may return overlapping groups (a blob in more than one) --
    assign_group_ids (PR #1070) collapses them via union-find, so all three
    blobs here end up flagged even though "a"/"c" never appear together."""
    session = _session()
    clusters = [{"group_id": "g1", "visit_ids": [111, 112, 113], "image_count": 3, "image_ids": ["a", "b", "c"]}]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
        "c": {"visit_id": 113, "question_id": "form/muac"},
    }
    client = Mock()
    client.detect.return_value = [["a", "b"], ["b", "c"]]

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    assert result["images_flagged"] == 3
    for visit_id, blob_id in (("111", "a"), ("112", "b"), ("113", "c")):
        assert session.data["visit_results"][visit_id]["assessments"][blob_id]["result"] == "duplicate_fake"


def test_skips_blobs_whose_signed_url_lookup_fails():
    session = _session()
    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
    }
    client = Mock()

    def flaky_signed_url(blob_id, opp_id):
        if blob_id == "b":
            return None  # simulates the endpoint 404ing pre-deploy
        return f"https://x/{blob_id}"

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=flaky_signed_url, client=client
    )

    # Only 1 of 2 images resolved a URL -- below the 2-image minimum, so the
    # whole grouping is skipped rather than sent with incomplete data.
    client.detect.assert_not_called()
    assert result["groupings_skipped"] == 1


def test_one_failed_grouping_does_not_stop_the_rest():
    from connect_labs.audit.duplicate_detection import DuplicateDetectionError

    session = _session()
    clusters = [
        {"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]},
        {"group_id": "g2", "visit_ids": [113, 114], "image_count": 2, "image_ids": ["c", "d"]},
    ]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
        "c": {"visit_id": 113, "question_id": "form/other"},
        "d": {"visit_id": 114, "question_id": "form/other"},
    }
    client = Mock()
    client.detect.side_effect = [DuplicateDetectionError("boom"), [["c", "d"]]]

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    assert result["errors"] == 1
    assert result["groupings_checked"] == 1
    assert result["images_flagged"] == 2
    assert session.data["visit_results"]["113"]["assessments"]["c"]["result"] == "duplicate_fake"


def test_non_duplicate_detection_error_from_api_call_does_not_propagate():
    session = _session()
    clusters = [
        {"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]},
        {"group_id": "g2", "visit_ids": [113, 114], "image_count": 2, "image_ids": ["c", "d"]},
    ]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
        "c": {"visit_id": 113, "question_id": "form/other"},
        "d": {"visit_id": 114, "question_id": "form/other"},
    }
    client = Mock()
    # A malformed-but-200 response could surface as a raw ValueError/JSONDecodeError
    # from inside detect() -- not a DuplicateDetectionError. Caught broadly.
    client.detect.side_effect = [ValueError("bad json"), [["c", "d"]]]

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    assert result["errors"] == 1
    assert result["groupings_checked"] == 1
    assert result["images_flagged"] == 2
    assert session.data["visit_results"]["113"]["assessments"]["c"]["result"] == "duplicate_fake"


def test_cancellation_stops_before_next_target():
    session_a = _session()
    session_b = _session()
    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
    }
    client = Mock()
    client.detect.return_value = []

    import connect_labs.audit.visit_cluster_duplicate_detection as vcdd_module

    calls = {"n": 0}

    def fake_cancelled(_key):
        calls["n"] += 1
        return calls["n"] > 1  # not cancelled for target 1, cancelled before target 2

    orig = vcdd_module.is_audit_creation_cancelled
    vcdd_module.is_audit_creation_cancelled = fake_cancelled
    try:
        result = run_grouping_duplicate_detection(
            [_target(session_a, clusters, blob_meta), _target(session_b, clusters, blob_meta)],
            get_signed_url=lambda bid, oid: f"https://x/{bid}",
            client=client,
            cancel_key="task-1",
        )
    finally:
        vcdd_module.is_audit_creation_cancelled = orig

    assert result["cancelled"] is True
    assert client.detect.call_count == 1


def test_progress_callback_receives_processed_and_total():
    session = _session()
    clusters = [
        {"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]},
        {"group_id": "g2", "visit_ids": [113], "image_count": 1, "image_ids": ["c"]},
    ]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
        "c": {"visit_id": 113, "question_id": "form/other"},
    }
    client = Mock()
    client.detect.return_value = []
    seen = []

    run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
        progress_callback=lambda processed, total, message: seen.append((processed, total)),
    )

    assert seen[-1] == (2, 2)


def test_run_duplicate_detection_preserves_ai_review_results_written_after_creation():
    """Regression test for a data-loss bug: `target["session"]` is whatever
    create_audit_session() returned at creation time (visit_results={}) --
    by the time this stage runs, the AI-review stage has already re-fetched
    and saved its OWN writes to the SAME session. save_audit_session() is a
    full-document replace, so mutating and saving the stale creation-time
    object here would silently wipe those AI-review results. This pins that
    the fresh copy (from data_access.get_audit_session(), not the stale
    `target["session"]`) is what actually gets mutated and saved."""
    stale_session_at_creation_time = _session()  # visit_results={} -- as create_audit_session() left it

    # What get_audit_session() would return by the time this stage runs --
    # already carries an AI reviewer's write for a visit unrelated to the
    # duplicate grouping below.
    fresh_session = _session(
        {
            "999": {
                "assessments": {
                    "z": {
                        "question_id": "form/muac",
                        "result": "fail",
                        "notes": "",
                        "ai_result": "no_match",
                        "ai_notes": "Hyperzoomed",
                    }
                }
            }
        }
    )

    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
    }
    client = Mock()
    client.detect.return_value = [["a", "b"]]
    data_access = Mock()
    data_access.get_audit_session.return_value = fresh_session

    result = run_grouping_duplicate_detection(
        [
            {
                "session": stale_session_at_creation_time,
                "data_access": data_access,
                "opp_id": 1973,
                "clusters": clusters,
                "blob_meta_by_id": blob_meta,
            }
        ],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
    )

    data_access.get_audit_session.assert_called_once_with(stale_session_at_creation_time.id)
    assert result["images_flagged"] == 2

    saved_session = data_access.save_audit_session.call_args[0][0]
    assert saved_session is fresh_session  # mutated the FRESH object, not the stale one

    # The AI reviewer's pre-existing write for visit 999 is untouched.
    preserved = saved_session.data["visit_results"]["999"]["assessments"]["z"]
    assert preserved["result"] == "fail"
    assert preserved["ai_notes"] == "Hyperzoomed"

    # The new duplicate flags landed alongside it, not instead of it.
    assert saved_session.data["visit_results"]["111"]["assessments"]["a"]["result"] == "duplicate_fake"
    assert saved_session.data["visit_results"]["112"]["assessments"]["b"]["result"] == "duplicate_fake"


def test_caps_images_per_grouping_and_counts_the_rest():
    """A grouping over the cap still runs (on its first N images) rather than
    being skipped outright -- the excess is counted, never silently dropped."""
    session = _session()
    image_ids = [f"img{i}" for i in range(5)]
    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 5, "image_ids": image_ids}]
    blob_meta = {bid: {"visit_id": 111, "question_id": "form/muac"} for bid in image_ids}
    client = Mock()
    client.detect.return_value = []

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
        max_images_per_grouping=3,
    )

    client.detect.assert_called_once()
    sent_ids = {img["id"] for img in client.detect.call_args[0][0]}
    assert sent_ids == {"img0", "img1", "img2"}
    assert result["skipped_over_limit"] == 2
    assert result["groupings_checked"] == 1


def test_default_image_cap_reads_from_shared_settings(settings):
    """Uses the SAME setting as PR #1070's day/FLW/type-bucketed detection
    (connect_labs.audit.duplicate_detection) rather than a second knob."""
    settings.DUPLICATE_DETECTION_MAX_IMAGES_PER_DAY = 2
    session = _session()
    image_ids = ["img0", "img1", "img2"]
    clusters = [{"group_id": "g1", "visit_ids": [111], "image_count": 3, "image_ids": image_ids}]
    blob_meta = {bid: {"visit_id": 111, "question_id": "form/muac"} for bid in image_ids}
    client = Mock()
    client.detect.return_value = []

    result = run_grouping_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    assert result["skipped_over_limit"] == 1
