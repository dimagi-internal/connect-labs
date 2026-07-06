from unittest import mock

import pytest

from connect_labs.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

TRACK_A = {
    "tag": "muac",
    "sample_percentage": 100,
    "reviewer": {
        "agent_id": "muac_overzoom",
        "auto_apply_actions": ["fail_overzoomed"],
    },
}
TRACK_B = {"tag": "rest", "sample_percentage": 10, "reviewer": None}


def test_builds_two_calls_per_opp_with_tags_and_image_audits():
    calls = build_track_audit_calls(
        opportunity_ids=[101, 102],
        opp_names={"101": "Opp A", "102": "Opp B"},
        per_opp={
            "101": {
                "muac_image_paths": ["form.muac"],
                "rest_image_paths": ["form.house", "form.id"],
            },
            "102": {
                "muac_image_paths": ["form.muac"],
                "rest_image_paths": ["form.house"],
            },
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


def _fake_run(state, definition_id=42):
    run = mock.Mock()
    run.is_completed = False
    run.definition_id = definition_id
    run.data = {"state": state}
    return run


def _fake_definition():
    d = mock.Mock()
    d.data = {
        "opportunity_ids": [101, 102],
        "config": {
            "audit_batch": {
                "track_a": TRACK_A,
                "track_b": TRACK_B,
                "per_opp": {
                    "101": {
                        "muac_image_paths": ["form.muac"],
                        "rest_image_paths": ["form.house"],
                    },
                    "102": {
                        "muac_image_paths": ["form.muac"],
                        "rest_image_paths": ["form.house"],
                    },
                },
                "opp_names": {"101": "Opp A", "102": "Opp B"},
            }
        },
    }
    return d


def test_handler_invokes_run_audit_creation_per_call_and_writes_summary():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}  # 3 FLWs

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        result = h.weekly_dual_track_audit_create({"run_id": 555, "opportunity_id": 101}, access_token="tok")

    assert rac.apply.call_count == 4  # 2 opps x 2 tracks
    assert result["successful"] == 4
    assert result["sessions_created"] == 12  # 4 calls x 3 sessions
    wda.update_run_state.assert_called_once()
    written = wda.update_run_state.call_args[0][1]
    assert written["window_start"] == "2026-06-22"  # window persisted onto the run for the PAR + reload
    assert written["last_batch"]["window_start"] == "2026-06-22"
    assert written["last_batch"]["calls"] == 4


def test_handler_reads_window_from_job_payload_when_state_lacks_it():
    """The render passes the window in the job payload, so audit creation works
    even when the best-effort run-state write flaked (state has no window)."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({})  # no window in run state
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        result = h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": 101, "window_start": "2026-06-22", "window_end": "2026-06-28"},
            access_token="tok",
        )

    assert rac.apply.call_count == 4  # window came from the payload; batch ran
    first_criteria = rac.apply.call_args_list[0].kwargs["kwargs"]["criteria"]
    assert first_criteria["start_date"] == "2026-06-22"
    assert first_criteria["end_date"] == "2026-06-28"
    assert result["successful"] == 4


def test_handler_relays_per_flw_progress_for_gliding_bar():
    """The handler passes a progress_callback into run_audit_creation so its
    fine-grained per-FLW progress reaches the caller (tag-prefixed) — that's what
    lets the program-creator row glide (e.g. 'muac · 3/3 field workers') instead of
    stepping once per track."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})

    seen = []

    def cb(msg, processed=0, total=0):
        seen.append((msg, processed, total))

    from connect_labs.audit.tasks import AUDIT_PROGRESS_RELAYS

    def fake_apply(kwargs=None):
        # Simulate run_audit_creation: look up the in-process relay by
        # workflow_run_id (NOT a serialized closure in kwargs) and fire per-FLW ticks.
        assert "progress_callback" not in (kwargs or {})  # never through .apply()
        relay = AUDIT_PROGRESS_RELAYS.get(555)
        if relay:
            relay("Creating audits · 1/3 field workers", processed=1, total=3)
            relay("Creating audits · 3/3 field workers", processed=3, total=3)
        eager = mock.Mock()
        eager.result = {"sessions": [1]}
        return eager

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.side_effect = fake_apply

        h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": 101}, access_token="tok", progress_callback=cb
        )

    # Per-FLW ticks reached the caller with processed/total for a gliding bar,
    # tag-prefixed so the row shows which track is generating.
    assert any(p == 1 and t == 3 for _, p, t in seen)
    assert any(p == 3 and t == 3 for _, p, t in seen)
    assert all(" · " in msg and "field workers" in msg for msg, _, _ in seen)
    # The registry is cleaned up after the run (no leaked relays).
    assert AUDIT_PROGRESS_RELAYS.get(555) is None


def test_template_registered_and_multi_opp():
    from connect_labs.workflow.templates import get_template

    tpl = get_template("weekly_dual_track_audit")
    assert tpl is not None
    assert tpl["multi_opp"] is True
    assert tpl["definition"]["templateType"] == "weekly_dual_track_audit"
    assert isinstance(tpl["render_code"], str) and "startJob" in tpl["render_code"]


def test_run_audit_creation_accepts_image_audits_contract():
    """Guard the cross-PR boundary: build_track_audit_calls emits image_audits /
    context_fields and the handler forwards them to run_audit_creation. The
    handler test mocks run_audit_creation, so without this non-mocked check a
    signature drift in the audit task (PR #771's per-image-type model) would go
    undetected. See plan Global Constraints + final review."""
    import inspect

    from connect_labs.audit.tasks import run_audit_creation

    params = inspect.signature(run_audit_creation).parameters
    assert "image_audits" in params
    assert "context_fields" in params


def test_handler_applies_per_run_sampling_override():
    """The render can pass MUAC/Other sampling % for a run; the handler overrides
    the pinned config defaults with them before building the audit calls."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "muac_sample_percentage": 50,  # config default is 100
                "other_sample_percentage": 25,  # config default is 10
            },
            access_token="tok",
        )

    by_tag = {}
    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        by_tag[cr["tag"]] = cr["sample_percentage"]
    assert by_tag["muac"] == 50
    assert by_tag["rest"] == 25


# ── Task 2: saved-runs completion-gate snapshot hook ─────────────────────────


def _sess(status, tag="muac", stats=None, img=10, fid="flw1"):
    s = mock.Mock()
    s.status = status
    s.tag = tag
    s.image_count = img
    s.id = 1
    s.opportunity_id = 1973
    s.flw_username = fid
    s.flw_display_name = fid
    s.get_assessment_stats.return_value = stats or {
        "pass": 0,
        "fail": 0,
        "pending": 0,
        "ai_no_match": 0,
    }
    return s


def test_build_snapshot_raises_until_all_audits_complete():
    from connect_labs.workflow.templates import weekly_dual_track_audit as m

    ada = mock.Mock()
    ada.get_sessions_by_workflow_run.return_value = [_sess("completed"), _sess("in_progress")]
    with mock.patch.object(m, "AuditDataAccess", return_value=ada):
        with pytest.raises(ValueError, match="1 of 2 audits still open"):
            m.build_snapshot(pipelines={}, state={}, opportunity_id=1973, run_id=55, access_token="t")


def test_build_snapshot_returns_rollup_when_all_complete():
    from connect_labs.workflow.templates import weekly_dual_track_audit as m

    ada = mock.Mock()
    ada.get_sessions_by_workflow_run.return_value = [
        _sess("completed", "muac", {"pass": 8, "fail": 2, "pending": 0, "ai_no_match": 2}, img=10),
        _sess("completed", "rest", {"pass": 5, "fail": 0, "pending": 0, "ai_no_match": 0}, img=5, fid="flw1"),
    ]
    with mock.patch.object(m, "AuditDataAccess", return_value=ada):
        snap = m.build_snapshot(
            pipelines={},
            state={"window_start": "2026-06-21"},
            opportunity_id=1973,
            run_id=55,
            access_token="t",
        )
    assert snap["completed_counts"]["total"] == 2
    assert snap["completed_counts"]["incomplete"] == 0
    assert snap["window_start"] == "2026-06-21"
    assert "flw1" in {r["flw_id"] for r in snap["audit_summary"]["flw_rows"]}
