"""Tests for the synthetic generator's audit + task archetype catalog."""

from connect_labs.labs.synthetic.archetypes import (
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


def _all_photos(data):
    """Flatten visit_images (one synthetic visit per photo) into one list."""
    return [img for imgs in data["visit_images"].values() for img in imgs]


def _all_assessments(data):
    """Flatten per-visit assessments into one {blob_id: assessment} dict."""
    out = {}
    for vr in data["visit_results"].values():
        out.update(vr.get("assessments", {}))
    return out


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
    # One synthetic visit per photo (the audit window derives from
    # per-visit dates), ids derived from the base.
    assert len(data["visit_ids"]) == 5
    assert len(set(data["visit_ids"])) == 5
    assert all(str(vid).startswith("9000001") for vid in data["visit_ids"])
    photos = _all_photos(data)
    assert len(photos) == 5
    # All photos should be from the good pool
    assert all("-good-" in p["blob_id"] for p in photos)
    # All photos should have a "pass" assessment recorded
    assessments = _all_assessments(data)
    assert len(assessments) == 5
    assert all(a["result"] == "pass" for a in assessments.values())
    # Every per-visit aggregate result agrees
    assert all(vr["result"] == "pass" for vr in data["visit_results"].values())


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
    photos = _all_photos(data)
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
    photos = _all_photos(data)
    assert len(photos) == 5
    # Only 2 photos should have assessments recorded across all visits
    assert len(_all_assessments(data)) == 2
    # Pending visits carry an empty aggregate result
    empty_results = [vr for vr in data["visit_results"].values() if vr["result"] == ""]
    assert len(empty_results) == 3
    # An in-progress audit must not claim a completion date
    assert "completed_at" not in data


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
    # Same seed → identical photo set, visit dates, entities, completion time.
    assert a == b


def test_audit_visits_spread_across_trailing_7_days():
    """The audited visits must span a true 7-day window ending at the
    anchor date — a single shared timestamp used to render
    'Nov 24, 2025 to Nov 24, 2025' for an 'Audit Last 7 days'."""
    from datetime import date, datetime

    data = build_audit_data(
        archetype_name="completed_pass_clean",
        flw_id="alice",
        monday_iso="2025-11-24",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=200,
        visit_id_base=9000020,
    )
    dates = sorted(datetime.fromisoformat(img["visit_date"]) for img in _all_photos(data))
    assert dates[0].date() == date(2025, 11, 18)  # anchor - 6 days
    assert dates[-1].date() == date(2025, 11, 24)  # the anchor day itself
    # Distinct days, not five copies of one timestamp
    assert len({d.date() for d in dates}) >= 3
    # No visit postdates the audit's creation moment
    created = datetime.fromisoformat(data["created_at"])
    assert all(d <= created for d in dates)
    # criteria advertises the same true range
    assert data["criteria"]["start_date"] == "2025-11-18"
    assert data["criteria"]["end_date"] == "2025-11-24"


def test_completed_audit_carries_completed_at():
    """'Completed on' on the audit page reads data['completed_at'] — a
    completed audit without it renders a blank completion line."""
    from datetime import datetime

    data = build_audit_data(
        archetype_name="completed_mixed_tape_usage",
        flw_id="bob",
        monday_iso="2025-11-10",
        opportunity_id=10001,
        opportunity_name="Demo Opp",
        workflow_run_id=200,
        visit_id_base=9000030,
    )
    completed = datetime.fromisoformat(data["completed_at"])
    created = datetime.fromisoformat(data["created_at"])
    assert completed > created
    assert completed.date() == created.date()  # reviewed the same day


def test_archetype_descriptions_are_story_true():
    """Archetype descriptions are written into user-visible record fields
    (audit notes/description, task description). Film-direction or
    scaffolding vocabulary must never appear there — it ends up on camera."""
    banned = ("demo", "filmed", "film", "camera", "smoke test", "synthetic", "recorder", "walkthrough", "scene")
    for catalog in (AUDIT_ARCHETYPES, TASK_ARCHETYPES):
        for name, arche in catalog.items():
            text = arche.description.lower()
            for word in banned:
                assert word not in text, f"{name} description leaks staging vocabulary: {word!r}"


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


def test_task_carries_archetype_appropriate_ocs_conversation():
    """build_task_data attaches an OCS coaching transcript matching the
    task's narrative outcome (closed_satisfactory → resolved-clean tone,
    closed_warned → formal warning, closed_suspended → suspension)."""
    for archetype_name in (
        "closed_satisfactory",
        "closed_warned",
        "closed_suspended",
        "closed_suspended_fraud",
        "investigating",
    ):
        data = build_task_data(
            archetype_name=archetype_name,
            flw_id="grace",
            monday_iso="2025-11-03",
            opportunity_id=10001,
            workflow_run_id=200,
            audit_session_id=300,
            title=f"[demo] {archetype_name}",
            creator_name="kwame_nm",
        )
        conv = data["ocs_conversation"]
        assert conv, f"{archetype_name} should have an ocs_conversation"
        assert len(conv) >= 4, f"{archetype_name} conversation too short ({len(conv)} msgs)"
        roles = {m["role"] for m in conv}
        assert roles == {"bot", "flw"}, f"{archetype_name} bad roles: {roles}"
        # First message must be from the bot (coach initiates)
        assert conv[0]["role"] == "bot"
        # FLW name placeholder must be filled in
        assert all("{flw_name}" not in m["text"] for m in conv)
        # Each message has an ISO timestamp
        from datetime import datetime

        for m in conv:
            datetime.fromisoformat(m["ts"])


def test_closed_task_transcript_close_agrees_with_history():
    """The coach's closing message must be stamped at (just before) the
    task History's close event — not days earlier. A transcript that says
    'Closing this task' on Nov 10 under a History that closes Nov 16 is
    the inconsistency this guards against."""
    from datetime import datetime, timedelta

    for archetype_name in ("closed_satisfactory", "closed_warned", "closed_suspended", "closed_suspended_fraud"):
        data = build_task_data(
            archetype_name=archetype_name,
            flw_id="grace",
            monday_iso="2025-11-03",
            opportunity_id=10001,
            workflow_run_id=200,
            audit_session_id=300,
            title=f"[demo] {archetype_name}",
            creator_name="kwame_nm",
        )
        closed_event = next(e for e in data["events"] if e["event_type"] == "closed")
        closed_at = datetime.fromisoformat(closed_event["timestamp"])
        last_msg_ts = datetime.fromisoformat(data["ocs_conversation"][-1]["ts"])
        assert last_msg_ts <= closed_at, f"{archetype_name}: closing message postdates the close event"
        assert closed_at - last_msg_ts <= timedelta(minutes=15), (
            f"{archetype_name}: closing message ({last_msg_ts}) is not aligned " f"with the close event ({closed_at})"
        )


def test_transcript_turns_are_not_metronomic():
    """Messages must not all land in the same minute / at one fixed
    interval — reply gaps should vary like a real conversation."""
    from datetime import datetime

    data = build_task_data(
        archetype_name="investigating",
        flw_id="frank",
        monday_iso="2025-11-17",
        opportunity_id=10001,
        workflow_run_id=340,
        audit_session_id=345,
        title="[demo] investigating",
        creator_name="kwame_nm",
    )
    stamps = [datetime.fromisoformat(m["ts"]) for m in data["ocs_conversation"]]
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    assert all(g > 0 for g in gaps), "timestamps must strictly increase"
    assert len(set(gaps)) > 1, "reply gaps must vary, not tick at a fixed interval"


def test_closed_satisfactory_transcript_tone_is_supportive():
    """The closed_satisfactory transcript should read as a friendly check-in,
    not a warning — checked by keyword in the bot's first message."""
    data = build_task_data(
        archetype_name="closed_satisfactory",
        flw_id="grace",
        monday_iso="2025-11-03",
        opportunity_id=10001,
        workflow_run_id=200,
        audit_session_id=300,
        title="[demo] satisfactory",
        creator_name="kwame_nm",
    )
    bot_messages = [m["text"] for m in data["ocs_conversation"] if m["role"] == "bot"]
    full = " ".join(bot_messages).lower()
    assert "small" in full or "refresher" in full or "great work" in full, full


def test_closed_warned_transcript_includes_formal_warning_language():
    data = build_task_data(
        archetype_name="closed_warned",
        flw_id="grace",
        monday_iso="2025-11-03",
        opportunity_id=10001,
        workflow_run_id=200,
        audit_session_id=300,
        title="[demo] warned",
        creator_name="kwame_nm",
    )
    bot_messages = " ".join(m["text"] for m in data["ocs_conversation"] if m["role"] == "bot").lower()
    assert "formal warning" in bot_messages or "warning" in bot_messages


def test_flw_pipeline_row_shape_matches_chc_nutrition_schema():
    """build_flw_pipeline_row produces all the fields chc_nutrition's render
    code reads from each row."""
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    row = build_flw_pipeline_row(
        flw_id="amina",
        archetype="solid",
        flagged_this_week=False,
        rng_seed=42,
    )
    required = {
        "username",
        "name",
        "total_visits",
        "approved_visits",
        "days_active",
        "muac_measurements_count",
        "muac_distribution_count",
        "muac_distribution_mean",
        "avg_muac_cm",
        "male_count",
        "female_count",
        "children_unwell_count",
        "under_malnutrition_treatment_count",
        "muac_9_5_10_5_visits",
        "muac_10_5_11_5_visits",
        "muac_11_5_12_5_visits",
        "muac_12_5_13_5_visits",
        "muac_13_5_14_5_visits",
        "muac_14_5_15_5_visits",
    }
    missing = required - set(row.keys())
    assert not missing, f"missing fields: {missing}"
    # Solid FLW (post PR #281 flag-direction flip): SAM bins seeded so the
    # row produces a baseline SAM presence (~3-7%) — too FEW SAM cases
    # would now trip sam_low. With weights [0, 2, 3, ...] and downward
    # jitter capped, bin 1 lands in [2, 3]. Allow the second SAM bin to
    # carry the floor; just ensure the overall MUAC mean stays in the
    # healthy range.
    assert row["avg_muac_cm"] >= 13.0


def test_flw_pipeline_row_suspended_fraudulent_skews_low():
    """suspended_fraudulent FLW in their flag week should look like
    cherry-picking — zero SAM mass, distribution shifted toward healthier
    arm circumferences. Post PR #281 the "fraudulent" signal is
    SAM/MAM = 0 (the FLW only visited well-fed children), not heavy
    SAM concentration.
    """
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    solid = build_flw_pipeline_row(flw_id="a", archetype="solid", flagged_this_week=False, rng_seed=1)
    fraud = build_flw_pipeline_row(flw_id="b", archetype="suspended_fraudulent", flagged_this_week=True, rng_seed=1)
    # Cherry-picking FLW: zero SAM bins, zero MAM bin — the bands the
    # flag predicates fire on.
    assert fraud["muac_9_5_10_5_visits"] == 0
    assert fraud["muac_10_5_11_5_visits"] == 0
    assert fraud["muac_11_5_12_5_visits"] == 0
    # Solid FLW: non-trivial presence in at least one SAM bin so SAM%
    # stays comfortably above the < 1% threshold.
    assert solid["muac_10_5_11_5_visits"] >= 1
    # And the cherry-picking FLW's mean should be at or above the solid
    # FLW's — they're skipping the low-arm cases that would pull the
    # mean down.
    assert fraud["avg_muac_cm"] >= solid["avg_muac_cm"]


# --------------------------------------------------------------------------- #
# Flag-coupling guard
# --------------------------------------------------------------------------- #
#
# These predicates MIRROR the FLAG_CATALOG in
# ``connect_labs/workflow/templates/chc_nutrition_analysis.py`` (the JSX
# render code). They are duplicated here in Python on purpose: the synthetic
# generator and the flag predicates are two halves of the same contract — the
# generator must produce rows that actually trip the flags the demo narrative
# says they trip. When PR #281 flipped the flag DIRECTION (sam_low went from
# "SAM too high" to "SAM < 1%"), nothing caught that the generator now produced
# the inverse of what it intended — we only found it by watching a recording.
#
# If you change the thresholds in chc_nutrition_analysis.py's FLAG_CATALOG,
# update these constants too; this test will fail loudly if the generator and
# the flag predicates drift out of agreement.
_SAM_LOW_PCT = 1.0  # FLAG_CATALOG sam_low: (samCount/muacCount)*100 < 1
_MAM_LOW_PCT = 3.0  # FLAG_CATALOG mam_low: (mamCount/muacCount)*100 < 3
_MIN_MUAC_FOR_FLAG = 10  # predicate floor: rows with < 10 measurements never flag


def _sam_count(r):
    return (r.get("muac_9_5_10_5_visits") or 0) + (r.get("muac_10_5_11_5_visits") or 0)


def _mam_count(r):
    return r.get("muac_11_5_12_5_visits") or 0


def _muac_count(r):
    return r.get("muac_distribution_count") or r.get("muac_measurements_count") or 0


def _trips_low_muac_flag(r) -> bool:
    """True if the row would trip sam_low OR mam_low under chc's FLAG_CATALOG."""
    mc = _muac_count(r)
    if mc < _MIN_MUAC_FOR_FLAG:
        return False
    sam_pct = (_sam_count(r) / mc) * 100
    mam_pct = (_mam_count(r) / mc) * 100
    return sam_pct < _SAM_LOW_PCT or mam_pct < _MAM_LOW_PCT


def test_flagged_muac_archetypes_actually_trip_a_flag():
    """A 'muac-flagged' archetype in its flag week must produce a row that
    trips sam_low/mam_low; a clean (solid) archetype must NOT.

    This is the guard that would have caught the PR #281 flag-direction
    inversion at the source instead of in a recording: it couples the
    synthetic generator to the live flag thresholds.
    """
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    # Clean baseline — must sit safely on the un-flagged side.
    for seed in range(20):
        solid = build_flw_pipeline_row(flw_id="s", archetype="solid", flagged_this_week=False, rng_seed=seed)
        assert not _trips_low_muac_flag(solid), f"solid row tripped a low-MUAC flag (seed={seed}): {solid}"

    # Each muac-flagging archetype, in its flag week, must trip a flag.
    flagged_cases = [
        ("improver_warned", {"flagged_this_week": True, "kpi_issue": "muac"}),
        ("improver_closed_satisfactory", {"flagged_this_week": True, "kpi_issue": "muac"}),
        ("suspended_repeat_offense", {"flagged_this_week": True}),
        ("suspended_fraudulent", {"flagged_this_week": True}),
    ]
    for archetype, kwargs in flagged_cases:
        for seed in range(20):
            row = build_flw_pipeline_row(flw_id="f", archetype=archetype, rng_seed=seed, **kwargs)
            assert _trips_low_muac_flag(row), f"{archetype} flag-week row did NOT trip a flag (seed={seed}): {row}"


def test_flw_pipeline_row_deterministic():
    """Same seed → same row, regenerations stable."""
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    a = build_flw_pipeline_row(flw_id="x", archetype="improver_warned", flagged_this_week=True, rng_seed=100)
    b = build_flw_pipeline_row(flw_id="x", archetype="improver_warned", flagged_this_week=True, rng_seed=100)
    assert a == b


def test_closed_suspended_fraud_uses_fraud_template():
    data = build_task_data(
        archetype_name="closed_suspended_fraud",
        flw_id="rina",
        monday_iso="2025-11-17",
        opportunity_id=10001,
        workflow_run_id=200,
        audit_session_id=300,
        title="[demo] fraud",
        creator_name="kwame_nm",
    )
    bot_messages = " ".join(m["text"] for m in data["ocs_conversation"] if m["role"] == "bot").lower()
    # The fraud template specifically mentions the photo-not-of-a-child finding
    assert "finger" in bot_messages or "fraud" in bot_messages or "real measurements" in bot_messages


# ---------------------------------------------------------------------------
# Reason-key conversation variants — a seeded coaching transcript must talk
# about the SAME issue the task's flag asserts (round-2 realism fixes).
# ---------------------------------------------------------------------------


def _task_for_reason(archetype_name: str, reason_key: str | None, flw_id: str = "isha_n") -> dict:
    return build_task_data(
        archetype_name=archetype_name,
        flw_id=flw_id,
        monday_iso="2026-05-04",
        opportunity_id=10000,
        workflow_run_id=77,
        audit_session_id=None,
        title=f"Coach {flw_id} — demo",
        creator_name="amani_nm",
        reason_key=reason_key,
    )


def _transcript_text(data: dict) -> str:
    return " ".join(m["text"].lower() for m in data["ocs_conversation"])


def test_gender_split_task_never_closes_on_photo_conversation():
    """The iter3 judge's showcase drill: isha_n's gender-split flag closed on
    a photo-framing conversation. The gender_skew variant must discuss the
    screening balance, never photo quality."""
    data = _task_for_reason("closed_satisfactory", "gender_skew")
    text = _transcript_text(data)
    assert "boys" in text or "girls" in text
    assert "framing" not in text
    assert "photo" not in text


def test_bad_muac_reason_gets_cherry_picking_conversation():
    """bad_muac_distribution means suspiciously-low SAM/MAM (cherry-picking
    easy households, post-PR-281 direction) — the transcript must be about
    household reach, not photo framing."""
    data = _task_for_reason("closed_satisfactory", "bad_muac_distribution")
    text = _transcript_text(data)
    assert "household" in text
    assert "framing" not in text


def test_every_demo_reason_combo_resolves_to_a_matched_variant():
    """Every (task archetype, reason_key) pair the PAR demo config can
    produce — except the repeated_failure suspensions, whose base templates
    already narrate the repeat offense — must resolve to a reason-specific
    conversation variant, not silently fall back to the base."""
    from connect_labs.labs.synthetic.generator.fixtures.ocs_templates import resolve_template_key

    combos = [
        ("closed_satisfactory", "bad_muac_distribution"),
        ("closed_satisfactory", "gender_skew"),
        ("closed_warned", "bad_muac_distribution"),
        ("closed_warned", "gender_skew"),
        ("closed_warned", "misleading_photos"),
        ("investigating", "bad_muac_distribution"),
        ("investigating", "gender_skew"),
    ]
    for archetype_name, reason_key in combos:
        base = TASK_ARCHETYPES[archetype_name].ocs_template_key
        resolved = resolve_template_key(base, reason_key)
        assert (
            resolved == f"{base}__{reason_key}"
        ), f"({archetype_name}, {reason_key}) fell back to {resolved!r} — add the variant"


def test_unknown_reason_key_falls_back_to_base_template():
    from connect_labs.labs.synthetic.generator.fixtures.ocs_templates import resolve_template_key

    assert resolve_template_key("coaching_resolved_clean", "some_future_reason") == "coaching_resolved_clean"
    assert resolve_template_key("coaching_resolved_clean", None) == "coaching_resolved_clean"
    assert resolve_template_key(None, "gender_skew") is None


def test_conversation_timestamps_never_predate_task_creation():
    """Every seeded transcript message must be stamped at or after the task's
    own created event — chat older than the task it belongs to is the
    instant canned-data tell the iter3 judge caught."""
    import datetime as dt

    for archetype_name in TASK_ARCHETYPES:
        for reason_key in (None, "gender_skew", "bad_muac_distribution"):
            data = _task_for_reason(archetype_name, reason_key)
            created = dt.datetime.fromisoformat(data["events"][0]["timestamp"])
            for msg in data["ocs_conversation"]:
                ts = dt.datetime.fromisoformat(msg["ts"])
                assert (
                    ts >= created
                ), f"{archetype_name}/{reason_key}: message at {ts} predates task creation {created}"
