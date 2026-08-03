"""Tests for the duplicate-photo detection assessment.

Covers the just-in-time signed-URL presign, the /detect_duplicates client,
union-find group assignment, the flag-only writeback (composing with existing AI
flags), and the per-(FLW, day, type) batching + 40-image cap in
run_duplicate_detection.
"""

import httpx
import pytest
from django.test import override_settings

from connect_labs.audit.duplicate_detection import (
    DuplicateDetectionClient,
    DuplicateDetectionError,
    assign_group_ids,
    build_duplicate_warnings,
    get_signed_url,
    run_duplicate_detection,
)
from connect_labs.audit.models import AuditSessionRecord


def _session(visit_images, opportunity_id=1):
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": opportunity_id,
            "data": {"visit_images": visit_images},
        }
    )


def _img(blob_id, question_id="form/muac_photo", day="2026-07-30", username="u1", **extra):
    return {
        "blob_id": blob_id,
        "question_id": question_id,
        "visit_date": f"{day}T09:00:00",
        "username": username,
        **extra,
    }


# --------------------------------------------------------------------------- #
# assign_group_ids
# --------------------------------------------------------------------------- #


def test_assign_group_ids_collapses_overlapping_groups():
    # a-b and b-c overlap on b -> one connected component.
    assert assign_group_ids([["a", "b"], ["b", "c"]]) == {"a": 0, "b": 0, "c": 0}


def test_assign_group_ids_separate_components_ordered_by_first_appearance():
    result = assign_group_ids([["a", "b"], ["c", "d"]])
    assert result == {"a": 0, "b": 0, "c": 1, "d": 1}


def test_assign_group_ids_drops_singletons():
    # A group of one is not a duplicate; it should not be flagged.
    assert assign_group_ids([["a"], ["b", "c"]]) == {"b": 0, "c": 0}


def test_assign_group_ids_empty():
    assert assign_group_ids([]) == {}


# --------------------------------------------------------------------------- #
# get_signed_url (minted just-in-time; raises on failure)
# --------------------------------------------------------------------------- #


def test_get_signed_url_returns_connect_value_on_success(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"attachment_signed_url": "https://s3.example/signed"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    assert get_signed_url(1, "blob-1", "tok") == "https://s3.example/signed"


def test_get_signed_url_raises_on_error(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "Client", _Client)
    with pytest.raises(httpx.ConnectError):
        get_signed_url(1, "blob-1", "tok")


def test_get_signed_url_raises_when_field_missing(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    with pytest.raises(ValueError):
        get_signed_url(1, "blob-1", "tok")


# --------------------------------------------------------------------------- #
# DuplicateDetectionClient.detect
# --------------------------------------------------------------------------- #


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_detect_parses_groups(monkeypatch):
    client = DuplicateDetectionClient()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"groups": [["a", "b"], ["b", "c"]]}

    monkeypatch.setattr(client.http_client, "post", lambda *a, **k: _Resp())
    assert client.detect([{"id": "a", "url": "u"}]) == [["a", "b"], ["b", "c"]]


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_detect_raises_on_rate_limit(monkeypatch):
    client = DuplicateDetectionClient()

    class _Resp:
        status_code = 429

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    monkeypatch.setattr(client.http_client, "post", lambda *a, **k: _Resp())
    with pytest.raises(DuplicateDetectionError):
        client.detect([{"id": "a", "url": "u"}])


@override_settings(SCALE_VALIDATION_API_KEY="")
def test_detect_raises_without_api_key():
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetectionClient().detect([{"id": "a", "url": "u"}])


def test_detect_empty_manifest():
    assert DuplicateDetectionClient().detect([]) == []


# --------------------------------------------------------------------------- #
# flag_potential_duplicate (writeback composes with existing AI flags)
# --------------------------------------------------------------------------- #


def test_flag_merges_with_existing_ai_flag():
    session = _session({})
    # Pre-existing muac_overzoom flag on the same blob.
    session.set_assessment(
        visit_id=10,
        blob_id="a",
        question_id="form/muac_photo",
        result=None,
        notes="",
        ai_result="no_match",
        ai_notes="Hyperzoomed",
    )
    session.flag_potential_duplicate(visit_id=10, blob_id="a", question_id="form/muac_photo", group_id=0)

    assessment = session.get_assessments(10)["a"]
    assert assessment["ai_result"] == "no_match"
    assert assessment["duplicate_group"] == 0
    assert "Hyperzoomed" in assessment["ai_notes"]
    assert "Potential Duplicate" in assessment["ai_notes"]
    # Human result untouched (flag only).
    assert assessment["result"] is None

    # get_assessment_stats splits ai_notes on the separator and counts BOTH.
    stats = session.get_assessment_stats()
    assert stats["ai_flags_by_label"]["Hyperzoomed"] == 1
    assert stats["ai_flags_by_label"]["Potential Duplicate"] == 1


def test_flag_is_idempotent_on_label():
    session = _session({})
    session.flag_potential_duplicate(visit_id=10, blob_id="a", question_id="q", group_id=1)
    session.flag_potential_duplicate(visit_id=10, blob_id="a", question_id="q", group_id=1)
    assert session.get_assessments(10)["a"]["ai_notes"] == "Potential Duplicate"


def test_flag_discards_stale_pass_label_instead_of_merging():
    """A classifier PASS ("Not Hyperzoomed") no longer applies once ai_result
    flips to "no_match" here -- merging it in used to leak the pass-label into
    get_assessment_stats().ai_flags_by_label as if it were a real flag."""
    session = _session({})
    session.set_assessment(
        visit_id=10,
        blob_id="a",
        question_id="form/muac_photo",
        result=None,
        notes="",
        ai_result="match",
        ai_notes="Not Hyperzoomed",
    )
    session.flag_potential_duplicate(visit_id=10, blob_id="a", question_id="form/muac_photo", group_id=0)

    assessment = session.get_assessments(10)["a"]
    assert assessment["ai_result"] == "no_match"
    assert assessment["ai_notes"] == "Potential Duplicate"

    stats = session.get_assessment_stats()
    assert "Not Hyperzoomed" not in stats["ai_flags_by_label"]
    assert stats["ai_flags_by_label"]["Potential Duplicate"] == 1


def test_flag_discards_stale_error_text_instead_of_merging():
    """A rate-limited (or otherwise errored) classifier call no longer applies
    once ai_result flips to "no_match" here -- merging the raw error message
    in used to leak it into ai_flags_by_label as if it were a real flag."""
    session = _session({})
    session.set_assessment(
        visit_id=10,
        blob_id="a",
        question_id="form/muac_photo",
        result=None,
        notes="",
        ai_result="error",
        ai_notes="Rate limited - service busy or starting up. Try again later.",
    )
    session.flag_potential_duplicate(visit_id=10, blob_id="a", question_id="form/muac_photo", group_id=0)

    assessment = session.get_assessments(10)["a"]
    assert assessment["ai_result"] == "no_match"
    assert assessment["ai_notes"] == "Potential Duplicate"


# --------------------------------------------------------------------------- #
# run_duplicate_detection (batching + cap + writeback)
# --------------------------------------------------------------------------- #


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_run_groups_by_day_and_type_and_flags(monkeypatch):
    # Two visits, same FLW/day/type; detector groups both blobs together.
    session = _session(
        {
            "100": [_img("a")],
            "101": [_img("b")],
        }
    )
    monkeypatch.setattr(
        "connect_labs.audit.duplicate_detection.get_signed_url",
        lambda opp, blob, tok: f"https://signed/{blob}",
    )
    monkeypatch.setattr(DuplicateDetectionClient, "detect", lambda self, manifest: [["a", "b"]])

    summary = run_duplicate_detection(session, access_token="tok")

    assert summary["images_flagged"] == 2
    assert summary["days_processed"] == 1
    assert session.get_assessments(100)["a"]["duplicate_group"] == 0
    assert session.get_assessments(101)["b"]["duplicate_group"] == 0
    assert session.data["duplicate_detection"]["form/muac_photo|2026-07-30"] == [["a", "b"]]


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_run_separates_distinct_days(monkeypatch):
    session = _session(
        {
            "100": [_img("a", day="2026-07-30")],
            "101": [_img("b", day="2026-07-31")],
        }
    )
    monkeypatch.setattr("connect_labs.audit.duplicate_detection.get_signed_url", lambda opp, blob, tok: "https://s")
    calls = []

    def _detect(self, manifest):
        calls.append([m["id"] for m in manifest])
        return []

    monkeypatch.setattr(DuplicateDetectionClient, "detect", _detect)
    summary = run_duplicate_detection(session, access_token="tok")

    assert summary["days_processed"] == 2
    # Each day is its own single-image batch.
    assert sorted(calls) == [["a"], ["b"]]


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_run_caps_at_max_per_day(monkeypatch):
    session = _session({str(i): [_img(f"b{i}")] for i in range(50)})
    monkeypatch.setattr("connect_labs.audit.duplicate_detection.get_signed_url", lambda opp, blob, tok: "https://s")
    sizes = []

    def _detect(self, manifest):
        sizes.append(len(manifest))
        return []

    monkeypatch.setattr(DuplicateDetectionClient, "detect", _detect)
    summary = run_duplicate_detection(session, access_token="tok", max_per_day=40)

    assert sizes == [40]
    # 50 images, cap 40 -> 10 skipped over the limit.
    assert summary["skipped_over_limit"] == 10


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_run_skips_image_on_presign_failure(monkeypatch):
    # Two images in one batch; the first blob's presign fails -> it drops out of
    # the manifest and is counted, the second still gets checked.
    session = _session({"100": [_img("a"), _img("b")]})

    def _signed(opp, blob, tok):
        if blob == "a":
            raise httpx.ConnectError("presign boom")
        return "https://signed/b"

    monkeypatch.setattr("connect_labs.audit.duplicate_detection.get_signed_url", _signed)
    seen = []

    def _detect(self, manifest):
        seen.append([m["id"] for m in manifest])
        return []

    monkeypatch.setattr(DuplicateDetectionClient, "detect", _detect)
    summary = run_duplicate_detection(session, access_token="tok")

    assert summary["skipped_presign"] == 1
    assert seen == [["b"]]  # only the successfully-presigned image was sent
    # Per-session summary + note is stashed on the session for the review banner.
    stored = session.data["duplicate_detection_summary"]
    assert stored["skipped_presign"] == 1
    assert "presigned-URL errors" in stored["note"]


@override_settings(SCALE_VALIDATION_API_KEY="k")
def test_run_counts_detect_failure(monkeypatch):
    session = _session({"100": [_img("a")]})
    monkeypatch.setattr("connect_labs.audit.duplicate_detection.get_signed_url", lambda opp, blob, tok: "https://s")

    def _detect(self, manifest):
        raise DuplicateDetectionError("rate limited")

    monkeypatch.setattr(DuplicateDetectionClient, "detect", _detect)
    summary = run_duplicate_detection(session, access_token="tok")

    assert summary["detect_failures"] == 1
    assert summary["images_flagged"] == 0
    # A failed batch is not recorded as a (false) empty result.
    assert "form/muac_photo|2026-07-30" not in session.data.get("duplicate_detection", {})


# --------------------------------------------------------------------------- #
# build_duplicate_warnings
# --------------------------------------------------------------------------- #


def test_build_duplicate_warnings_clean_run():
    assert build_duplicate_warnings({"skipped_presign": 0, "detect_failures": 0}) == ([], "")


def test_build_duplicate_warnings_all_kinds():
    warnings, note = build_duplicate_warnings(
        {"detect_failures": 1, "skipped_presign": 2, "skipped_over_limit": 3, "session_errors": 1},
        max_per_day=40,
    )
    assert len(warnings) == 4
    assert note.startswith("Duplicate detection completed with issues:")
    assert "40/day limit" in note


# --------------------------------------------------------------------------- #
# _run_duplicate_detection_on_sessions -- run-summary note assembly
# --------------------------------------------------------------------------- #


class _FakeDataAccess:
    def get_audit_session(self, sid):
        return {"id": sid}

    def save_audit_session(self, session):
        pass


def test_run_summary_note_lists_every_failure_kind(monkeypatch):
    from connect_labs.audit import tasks

    def _fake_run(session, access_token, progress_callback=None):
        return {
            "groups_detected": 0,
            "images_flagged": 0,
            "days_processed": 1,
            "skipped_over_limit": 3,
            "skipped_presign": 2,
            "detect_failures": 1,
        }

    monkeypatch.setattr("connect_labs.audit.duplicate_detection.run_duplicate_detection", _fake_run)

    totals = tasks._run_duplicate_detection_on_sessions(_FakeDataAccess(), [1], "tok")

    assert totals["skipped_presign"] == 2
    assert totals["detect_failures"] == 1
    assert totals["skipped_over_limit"] == 3
    assert "1 day-batch(es) failed the duplicate check" in totals["note"]
    assert "2 image(s) skipped due to presigned-URL errors" in totals["note"]
    assert "40/day limit" in totals["note"]
    assert len(totals["warnings"]) == 3


def test_run_summary_note_counts_session_errors(monkeypatch):
    from connect_labs.audit import tasks

    def _boom(session, access_token, progress_callback=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("connect_labs.audit.duplicate_detection.run_duplicate_detection", _boom)

    totals = tasks._run_duplicate_detection_on_sessions(_FakeDataAccess(), [1, 2], "tok")

    assert totals["session_errors"] == 2
    assert "2 session(s) errored during duplicate detection" in totals["note"]


def test_run_summary_note_empty_on_clean_run(monkeypatch):
    from connect_labs.audit import tasks

    def _clean(session, access_token, progress_callback=None):
        return {
            "groups_detected": 1,
            "images_flagged": 2,
            "days_processed": 1,
            "skipped_over_limit": 0,
            "skipped_presign": 0,
            "detect_failures": 0,
        }

    monkeypatch.setattr("connect_labs.audit.duplicate_detection.run_duplicate_detection", _clean)

    totals = tasks._run_duplicate_detection_on_sessions(_FakeDataAccess(), [1], "tok")

    assert totals["note"] == ""
    assert totals["warnings"] == []
