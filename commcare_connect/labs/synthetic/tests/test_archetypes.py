"""Tests for the synthetic generator's audit + task archetype catalog."""

from commcare_connect.labs.synthetic.archetypes import (
    AUDIT_ARCHETYPES,
    TASK_ARCHETYPES,
    bad_muac_filenames_for_category,
    blob_id_for_filename,
    build_audit_data,
    build_task_data,
)


def test_blob_id_for_filename_translates_to_image_server_pattern():
    assert blob_id_for_filename("muac_good_003.jpg") == "synth-muac-good-003"
    assert blob_id_for_filename("muac_bad_017.jpg") == "synth-muac-bad-017"


def test_bad_muac_filenames_for_category_returns_only_that_category():
    tape = bad_muac_filenames_for_category("tape_usage")
    assert tape, "expected at least one tape_usage entry in the corpus"
    assert all(f.startswith("muac_bad_") for f in tape)
    # Other categories shouldn't bleed in
    framing = bad_muac_filenames_for_category("framing")
    assert framing
    assert set(tape).isdisjoint(framing)


def test_audit_completed_pass_clean_attaches_5_good_images():
    data = build_audit_data(
        archetype_name="completed_pass_clean",
        flw_id="alice",
        monday_iso="2025-11-03",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=200,
        visit_id_base=9000001,
    )
    assert data["status"] == "completed"
    assert data["overall_result"] == "pass"
    assert data["image_results"] == {"pass": 5, "fail": 0, "pending": 0}
    photos = data["visit_images"]["9000001"]
    assert len(photos) == 5
    # All photos should be from the good pool
    assert all("-good-" in p["blob_id"] for p in photos)
    # All photos should have a "pass" assessment recorded
    assessments = data["visit_results"]["9000001"]["assessments"]
    assert len(assessments) == 5
    assert all(a["result"] == "pass" for a in assessments.values())


def test_audit_completed_fail_misleading_prefers_misleading_category():
    data = build_audit_data(
        archetype_name="completed_fail_misleading",
        flw_id="bob",
        monday_iso="2025-11-03",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=200,
        visit_id_base=9000002,
    )
    assert data["status"] == "completed"
    assert data["overall_result"] == "fail"
    assert data["image_results"] == {"pass": 0, "fail": 5, "pending": 0}
    photos = data["visit_images"]["9000002"]
    # All 5 photos are from the bad pool (the catalog is only ~13 photos
    # across 5 categories, so a 5-fail audit naturally tops up beyond the
    # primary category — but at least some must be from misleading).
    assert len(photos) == 5
    misleading = set(bad_muac_filenames_for_category("misleading"))
    misleading_blob_ids = {f"synth-muac-bad-{f.split('_')[-1].removesuffix('.jpg')}" for f in misleading}
    chosen_blob_ids = {p["blob_id"] for p in photos}
    overlap = chosen_blob_ids & misleading_blob_ids
    assert overlap, "expected at least one misleading photo in the chosen set"
    for p in photos:
        assert "-bad-" in p["blob_id"]


def test_audit_in_review_partial_has_pending_photos_no_overall_result():
    data = build_audit_data(
        archetype_name="in_review_partial",
        flw_id="carol",
        monday_iso="2025-11-17",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=300,
        visit_id_base=9000003,
    )
    assert data["status"] == "in_progress"
    assert data["overall_result"] is None
    # 1 pass + 1 fail = 2 assessed; 3 pending = 5 total
    assert data["image_results"]["pending"] == 3
    assert data["image_results"]["pass"] + data["image_results"]["fail"] == 2
    photos = data["visit_images"]["9000003"]
    assert len(photos) == 5
    # visit_results.result should be empty (no aggregate yet)
    assert data["visit_results"]["9000003"]["result"] == ""
    # Only 2 photos should have assessments recorded
    assert len(data["visit_results"]["9000003"]["assessments"]) == 2


def test_audit_data_is_deterministic_for_same_seed():
    kw = dict(
        archetype_name="completed_mixed_tape_usage",
        flw_id="dave",
        monday_iso="2025-11-10",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=290,
        visit_id_base=9000010,
    )
    a = build_audit_data(**kw)
    b = build_audit_data(**kw)
    assert [p["blob_id"] for p in a["visit_images"]["9000010"]] == [
        p["blob_id"] for p in b["visit_images"]["9000010"]
    ]


def test_task_closed_warned_has_close_event_and_resolution():
    data = build_task_data(
        archetype_name="closed_warned",
        flw_id="emma",
        monday_iso="2025-11-03",
        opportunity_id=10001,
        workflow_run_id=324,
        audit_session_id=327,
        title="[Gender skew] emma",
        creator_name="kwame_nm",
    )
    assert data["status"] == "closed"
    assert data["resolution_details"]["official_action"] == "warned"
    event_types = [e["event_type"] for e in data["events"]]
    assert event_types == ["created", "closed"]
    # Closed event should be 5 days + 4 hours after created
    from datetime import datetime
    created = datetime.fromisoformat(data["events"][0]["timestamp"])
    closed = datetime.fromisoformat(data["events"][1]["timestamp"])
    assert (closed - created).days == 5


def test_task_investigating_has_no_close_event():
    data = build_task_data(
        archetype_name="investigating",
        flw_id="frank",
        monday_iso="2025-11-17",
        opportunity_id=10001,
        workflow_run_id=340,
        audit_session_id=345,
        title="[Bad MUAC] frank",
        creator_name="kwame_nm",
    )
    assert data["status"] == "investigating"
    assert data["resolution_details"] == {}
    event_types = [e["event_type"] for e in data["events"]]
    assert event_types == ["created"]


def test_all_audit_archetypes_have_descriptions():
    for name, arche in AUDIT_ARCHETYPES.items():
        assert arche.description, f"{name} missing description"
        assert arche.status in {"in_progress", "completed"}


def test_all_task_archetypes_have_descriptions():
    for name, arche in TASK_ARCHETYPES.items():
        assert arche.description, f"{name} missing description"
        assert arche.status in {"investigating", "closed"}
