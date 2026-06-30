from commcare_connect.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

TRACK_A = {
    "tag": "muac",
    "sample_percentage": 100,
    "reviewer": {"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"]},
}
TRACK_B = {"tag": "rest", "sample_percentage": 10, "reviewer": None}


def test_builds_two_calls_per_opp_with_tags_and_image_audits():
    calls = build_track_audit_calls(
        opportunity_ids=[101, 102],
        opp_names={"101": "Opp A", "102": "Opp B"},
        per_opp={
            "101": {"muac_image_paths": ["form.muac"], "rest_image_paths": ["form.house", "form.id"]},
            "102": {"muac_image_paths": ["form.muac"], "rest_image_paths": ["form.house"]},
        },
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert len(calls) == 4  # 2 opps x 2 tracks

    a = next(c for c in calls if c["opportunities"][0]["id"] == 101 and c["criteria"]["tag"] == "muac")
    assert a["criteria"]["granularity"] == "per_flw"
    assert a["criteria"]["sample_percentage"] == 100
    assert a["criteria"]["audit_type"] == "date_range"
    assert a["criteria"]["start_date"] == "2026-06-22"
    assert a["criteria"]["end_date"] == "2026-06-28"
    # PR #771 model: reviewer rides inside image_audits; no related_fields / ai_agent_id emitted.
    assert "related_fields" not in a["criteria"]
    assert "ai_agent_id" not in a
    assert a["image_audits"] == [
        {
            "image_path": "form.muac",
            "reviewers": [{"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"]}],
        }
    ]
    assert a["context_fields"] is None
    assert a["workflow_run_id"] == 555
    assert a["opportunities"][0]["name"] == "Opp A"

    b = next(c for c in calls if c["opportunities"][0]["id"] == 101 and c["criteria"]["tag"] == "rest")
    assert b["criteria"]["sample_percentage"] == 10
    # Track B has no reviewer: every image_audits entry carries an empty reviewers list.
    assert all(e["reviewers"] == [] for e in b["image_audits"])
    assert {e["image_path"] for e in b["image_audits"]} == {"form.house", "form.id"}


def test_skips_track_with_no_image_paths():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"], "rest_image_paths": []}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert len(calls) == 1
    assert calls[0]["criteria"]["tag"] == "muac"
