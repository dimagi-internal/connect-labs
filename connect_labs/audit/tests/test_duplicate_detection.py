"""Unit tests for connect_labs.audit.duplicate_detection.

Uses a minimal fake session object (a plain object with a `.data` dict,
matching AuditSessionRecord's shape closely enough for assessment
read/modify/write) rather than the full AuditSessionRecord/LocalLabsRecord
machinery -- these tests are about the merge/dispatch logic, not persistence.
"""

from unittest.mock import Mock

import pytest

from connect_labs.audit.duplicate_detection import (
    DUPLICATE_DETECTED_LABEL,
    _mark_duplicate,
    run_duplicate_detection,
)
from connect_labs.labs.integrations.duplicate_detection.api_client import DuplicateDetectionError


class FakeSession:
    def __init__(self, data=None):
        self.id = 1
        self.data = data or {}


def _target(session, clusters, blob_meta_by_id, opp_id=1973, data_access=None):
    return {
        "session": session,
        "data_access": data_access or Mock(),
        "opp_id": opp_id,
        "clusters": clusters,
        "blob_meta_by_id": blob_meta_by_id,
    }


# ── _mark_duplicate ──────────────────────────────────────────────────────


def test_mark_duplicate_on_untouched_assessment_sets_result_and_ai_fields():
    session = FakeSession()
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    assert _mark_duplicate(session, blob_meta, "a") is True

    assessment = session.data["visit_results"]["111"]["assessments"]["a"]
    assert assessment["result"] == "duplicate_fake"
    assert assessment["ai_result"] == "no_match"
    assert assessment["ai_notes"] == DUPLICATE_DETECTED_LABEL
    assert assessment["question_id"] == "form/muac"


def test_mark_duplicate_never_overwrites_existing_human_result():
    session = FakeSession(
        {"visit_results": {"111": {"assessments": {"a": {"question_id": "form/muac", "result": "pass", "notes": ""}}}}}
    )
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a")

    assert session.data["visit_results"]["111"]["assessments"]["a"]["result"] == "pass"
    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_result"] == "no_match"


def test_mark_duplicate_appends_to_existing_ai_notes_without_duplicating_label():
    session = FakeSession(
        {
            "visit_results": {
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
        }
    )
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a")
    assessment = session.data["visit_results"]["111"]["assessments"]["a"]
    assert assessment["ai_notes"] == "Hyperzoomed; Duplicate Detected"

    # Idempotent -- marking the same blob twice doesn't duplicate the label.
    _mark_duplicate(session, blob_meta, "a")
    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_notes"] == "Hyperzoomed; Duplicate Detected"


def test_mark_duplicate_preserves_error_ai_result_instead_of_downgrading():
    session = FakeSession(
        {
            "visit_results": {
                "111": {
                    "assessments": {
                        "a": {"question_id": "form/muac", "result": None, "notes": "", "ai_result": "error"}
                    }
                }
            }
        }
    )
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}

    _mark_duplicate(session, blob_meta, "a")

    assert session.data["visit_results"]["111"]["assessments"]["a"]["ai_result"] == "error"


def test_mark_duplicate_returns_false_for_unknown_blob():
    session = FakeSession()
    assert _mark_duplicate(session, {}, "unknown-blob") is False
    assert session.data == {}


# ── run_duplicate_detection ──────────────────────────────────────────────


def test_skips_groupings_with_fewer_than_two_images():
    session = FakeSession()
    clusters = [{"group_id": "g1", "visit_ids": [111], "image_count": 1, "image_ids": ["a"]}]
    blob_meta = {"a": {"visit_id": 111, "question_id": "form/muac"}}
    client = Mock()

    result = run_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    client.detect_duplicates.assert_not_called()
    assert result == {"groupings_checked": 0, "groupings_skipped": 1, "images_flagged": 0, "errors": 0, "cancelled": False}


def test_calls_api_once_per_grouping_and_flags_returned_ids():
    session = FakeSession()
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
    client.detect_duplicates.side_effect = [{"groups": [["a", "b"]]}, {"groups": []}]
    data_access = Mock()

    result = run_duplicate_detection(
        [_target(session, clusters, blob_meta, data_access=data_access)],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
    )

    assert client.detect_duplicates.call_count == 2
    first_call_images = client.detect_duplicates.call_args_list[0][0][0]
    assert {img["id"] for img in first_call_images} == {"a", "b"}
    assert result["groupings_checked"] == 2
    assert result["groupings_skipped"] == 0
    assert result["images_flagged"] == 2
    assert result["errors"] == 0
    assert session.data["visit_results"]["111"]["assessments"]["a"]["result"] == "duplicate_fake"
    assert session.data["visit_results"]["112"]["assessments"]["b"]["result"] == "duplicate_fake"
    assert "113" not in session.data["visit_results"]
    data_access.save_audit_session.assert_called_once_with(session)


def test_skips_blobs_whose_signed_url_lookup_fails():
    session = FakeSession()
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

    result = run_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=flaky_signed_url, client=client
    )

    # Only 1 of 2 images resolved a URL -- below the 2-image minimum, so the
    # whole grouping is skipped rather than sent with incomplete data.
    client.detect_duplicates.assert_not_called()
    assert result["groupings_skipped"] == 1


def test_one_failed_grouping_does_not_stop_the_rest():
    session = FakeSession()
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
    client.detect_duplicates.side_effect = [DuplicateDetectionError("boom"), {"groups": [["c", "d"]]}]

    result = run_duplicate_detection(
        [_target(session, clusters, blob_meta)], get_signed_url=lambda bid, oid: f"https://x/{bid}", client=client
    )

    assert result["errors"] == 1
    assert result["groupings_checked"] == 1
    assert result["images_flagged"] == 2
    assert session.data["visit_results"]["113"]["assessments"]["c"]["result"] == "duplicate_fake"


def test_cancellation_stops_before_next_target():
    session_a = FakeSession()
    session_b = FakeSession()
    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}]
    blob_meta = {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
    }
    client = Mock()
    client.detect_duplicates.return_value = {"groups": []}

    import connect_labs.audit.duplicate_detection as dd_module

    calls = {"n": 0}

    def fake_cancelled(_key):
        calls["n"] += 1
        return calls["n"] > 1  # not cancelled for target 1, cancelled before target 2

    orig = dd_module.is_audit_creation_cancelled
    dd_module.is_audit_creation_cancelled = fake_cancelled
    try:
        result = run_duplicate_detection(
            [_target(session_a, clusters, blob_meta), _target(session_b, clusters, blob_meta)],
            get_signed_url=lambda bid, oid: f"https://x/{bid}",
            client=client,
            cancel_key="task-1",
        )
    finally:
        dd_module.is_audit_creation_cancelled = orig

    assert result["cancelled"] is True
    assert client.detect_duplicates.call_count == 1


def test_progress_callback_receives_processed_and_total():
    session = FakeSession()
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
    client.detect_duplicates.return_value = {"groups": []}
    seen = []

    run_duplicate_detection(
        [_target(session, clusters, blob_meta)],
        get_signed_url=lambda bid, oid: f"https://x/{bid}",
        client=client,
        progress_callback=lambda processed, total, message: seen.append((processed, total)),
    )

    assert seen[-1] == (2, 2)
